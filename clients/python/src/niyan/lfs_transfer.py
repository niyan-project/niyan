import hashlib
import http.client
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

from niyan.auth import select_credential
from niyan.errors import ApiError, GitError, NiyanCliError
from niyan.git import load_checkout_identity
from niyan.http import ApiClient


MAX_PROTOCOL_LINE_BYTES = 1024 * 1024
MAX_PART_WORKERS = 4
MAX_TRANSFER_ATTEMPTS = 3
OID_PATTERN = re.compile(r'^[0-9a-f]{64}$')
TRANSIENT_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}


class TransferFailure(NiyanCliError):
    """Report one sanitized custom-transfer failure."""


class RetryableTransferFailure(TransferFailure):
    """Report a transient direct-transfer failure eligible for retry."""


class ProtocolWriter:
    """Serialize line-delimited Git LFS protocol output across worker threads."""

    def __init__(self, stream):
        """Initialize a synchronized protocol output stream.

        Parameters
        ----------
        stream : text file-like object
            Git LFS agent standard output.
        """

        self.stream = stream
        self.lock = threading.Lock()
        self.progress = {}

    def send(self, payload):
        """Write and flush one compact JSON protocol message."""

        encoded = json.dumps(payload, separators=(',', ':'))
        with self.lock:
            self.stream.write(f'{encoded}\n')
            self.stream.flush()

    def add_progress(self, oid, byte_count):
        """Report one successfully transferred byte range."""

        with self.lock:
            total = self.progress.get(oid, 0) + byte_count
            self.progress[oid] = total
            encoded = json.dumps({'event': 'progress', 'oid': oid, 'bytesSoFar': total, 'bytesSinceLast': byte_count}, separators=(',', ':'))
            self.stream.write(f'{encoded}\n')
            self.stream.flush()

    def reset_progress(self, oid):
        """Begin a fresh attempt for one object identifier."""

        with self.lock:
            self.progress[oid] = 0


class DirectUploader:
    """Stream exact byte ranges directly to signed HTTP PUT actions."""

    def __init__(self, *, timeout=60):
        """Set the per-request socket timeout.

        Parameters
        ----------
        timeout : int or float, optional
            Network timeout in seconds.
        """

        self.timeout = timeout

    def put(self, *, action, path, offset, size):
        """Upload one exact local byte range and return bounded provider metadata.

        Parameters
        ----------
        action : dict
            Signed action containing ``href`` and optional ``header`` values.
        path : pathlib.Path
            Local Git LFS object path.
        offset : int
            Starting byte offset.
        size : int
            Exact number of bytes to send.

        Returns
        -------
        dict
            ETag and supported provider checksum response headers.
        """

        url, headers = _validated_action(action, expected_size=size)
        parsed = urlparse(url)
        connection_class = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
        connection = connection_class(parsed.hostname, parsed.port, timeout=self.timeout)
        target = parsed.path or '/'
        if parsed.query:
            target = f'{target}?{parsed.query}'
        try:
            connection.putrequest('PUT', target)
            for name, value in headers.items():
                connection.putheader(name, value)
            if not any(name.lower() == 'content-length' for name in headers):
                connection.putheader('Content-Length', str(size))
            connection.endheaders()
            with Path(path).open('rb') as stream:
                stream.seek(offset)
                remaining = size
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise TransferFailure('The local Git LFS object ended before its declared size.')
                    connection.send(chunk)
                    remaining -= len(chunk)
            response = connection.getresponse()
            response.read(64 * 1024)
            if response.status in TRANSIENT_HTTP_STATUSES or response.status in {401, 403}:
                raise RetryableTransferFailure('Object storage temporarily rejected a direct upload.')
            if not 200 <= response.status < 300:
                raise TransferFailure(f'Object storage rejected a direct upload with HTTP {response.status}.')
            return {
                'etag': response.getheader('ETag'),
                'checksum_sha256': response.getheader('x-amz-checksum-sha256'),
                'checksum_crc32c': response.getheader('x-amz-checksum-crc32c'),
                'checksum_crc32': response.getheader('x-amz-checksum-crc32'),
            }
        except RetryableTransferFailure:
            raise
        except (OSError, http.client.HTTPException) as error:
            raise RetryableTransferFailure('Could not complete a direct object-storage upload.') from error
        finally:
            connection.close()


class UploadTransfer:
    """Execute single-PUT and multipart upload actions for one checkout."""

    def __init__(self, *, identity, token, writer, api_factory=ApiClient, uploader=None, sleep=time.sleep, workers=MAX_PART_WORKERS, attempts=MAX_TRANSFER_ATTEMPTS):
        """Initialize checkout-scoped transfer dependencies.

        Parameters
        ----------
        identity : niyan.config.CheckoutIdentity
            Validated checkout identity.
        token : str
            Selected Niyān access token.
        writer : ProtocolWriter
            Git LFS protocol response writer.
        api_factory : callable, optional
            Niyān API client constructor.
        uploader : DirectUploader, optional
            Direct signed-action executor.
        sleep : callable, optional
            Retry delay dependency.
        workers : int, optional
            Maximum multipart part workers.
        attempts : int, optional
            Maximum attempts for each retryable operation.
        """

        self.identity = identity
        self.token = token
        self.writer = writer
        self.api_factory = api_factory
        self.uploader = uploader or DirectUploader()
        self.sleep = sleep
        self.workers = max(1, min(int(workers), MAX_PART_WORKERS))
        self.attempts = max(1, int(attempts))

    def upload(self, request):
        """Validate and execute one Git LFS upload request."""

        oid, size, path, action = _validated_upload_request(request)
        fingerprint = _verify_local_object(path=path, oid=oid, size=size)
        if self._is_multipart_action(action, oid):
            self._multipart_upload(oid=oid, size=size, path=path, fingerprint=fingerprint, initiation_url=action['href'])
        else:
            self._single_put(oid=oid, size=size, path=path, fingerprint=fingerprint, action=action)

    def _single_put(self, *, oid, size, path, fingerprint, action):
        """Execute one ordinary signed PUT selected in a mixed custom batch."""

        for attempt in range(self.attempts):
            try:
                self.uploader.put(action=action, path=path, offset=0, size=size)
                _verify_local_fingerprint(path, fingerprint)
                self.writer.add_progress(oid, size)
                return
            except RetryableTransferFailure:
                if attempt + 1 == self.attempts:
                    raise
                self.sleep(0.5 * (2**attempt))

    def _multipart_upload(self, *, oid, size, path, fingerprint, initiation_url):
        """Coordinate one server-controlled multipart upload without proxying bytes."""

        session = self._control('POST', initiation_url, {'size': size})
        session_id, part_size, part_count = _validated_session(session, oid=oid, size=size)
        parts = []
        try:
            with ThreadPoolExecutor(max_workers=min(self.workers, part_count)) as executor:
                futures = {}
                for part_number in range(1, part_count + 1):
                    offset = (part_number - 1) * part_size
                    length = min(part_size, size - offset)
                    future = executor.submit(self._upload_part, oid=oid, path=path, initiation_url=initiation_url, session_id=session_id, part_number=part_number, offset=offset, size=length)
                    futures[future] = length
                for future in as_completed(futures):
                    parts.append(future.result())
                    self.writer.add_progress(oid, futures[future])
            parts.sort(key=lambda part: part['part_number'])
            _verify_local_fingerprint(path, fingerprint)
            completed = self._control('POST', f'{initiation_url}/{session_id}/complete', {'parts': parts})
            if completed.get('session_id') != session_id or completed.get('oid') != oid or completed.get('size') != size or completed.get('state') != 'completed':
                raise TransferFailure('Niyān returned invalid multipart completion metadata.')
        except Exception:
            self._abort(initiation_url=initiation_url, session_id=session_id)
            raise

    def _upload_part(self, *, oid, path, initiation_url, session_id, part_number, offset, size):
        """Acquire and retry one exact-size signed multipart part action."""

        for attempt in range(self.attempts):
            try:
                action = self._control('POST', f'{initiation_url}/{session_id}/parts/{part_number}', {'size': size}, attempts=1)
                result = self.uploader.put(action=action, path=path, offset=offset, size=size)
                if not result.get('etag'):
                    raise TransferFailure('Object storage did not return the ETag required for multipart completion.')
                part = {'part_number': part_number, 'etag': result['etag']}
                for name in ('checksum_sha256', 'checksum_crc32c', 'checksum_crc32'):
                    if result.get(name):
                        part[name] = result[name]
                return part
            except (RetryableTransferFailure, ApiError) as error:
                retryable = isinstance(error, RetryableTransferFailure) or error.status in TRANSIENT_HTTP_STATUSES
                if not retryable or attempt + 1 == self.attempts:
                    raise
                self.sleep(0.5 * (2**attempt))
        raise TransferFailure('Multipart part upload attempts were exhausted.')

    def _control(self, method, url, payload=None, *, attempts=None):
        """Call one same-origin Bearer-authenticated multipart endpoint with retries."""

        limit = self.attempts if attempts is None else attempts
        for attempt in range(limit):
            try:
                _, response = self.api_factory(self.identity.host, token=self.token).request(method, url, payload=payload)
                return response
            except ApiError as error:
                if error.status not in TRANSIENT_HTTP_STATUSES or attempt + 1 == limit:
                    raise
                self.sleep(0.5 * (2**attempt))
        raise TransferFailure('Multipart control attempts were exhausted.')

    def _abort(self, *, initiation_url, session_id):
        """Best-effort abort one failed provider multipart upload."""

        try:
            self._control('DELETE', f'{initiation_url}/{session_id}', attempts=1)
        except (ApiError, TransferFailure):
            return

    def _is_multipart_action(self, action, oid):
        """Recognize only this checkout's exact Niyān multipart initiation URL."""

        parsed_action = urlparse(action.get('href', ''))
        parsed_host = urlparse(self.identity.host)
        expected_path = f'/api/v1/datasets/{self.identity.dataset_id}/lfs/objects/{oid}/multipart'
        same_origin = parsed_action.scheme.lower() == parsed_host.scheme.lower() and parsed_action.netloc.lower() == parsed_host.netloc.lower()
        if same_origin and parsed_action.path.startswith('/api/'):
            if parsed_action.path == expected_path and not parsed_action.params and not parsed_action.query and not parsed_action.fragment:
                return True
            raise TransferFailure('Niyān returned an invalid multipart control URL.')
        return False


def run_lfs_transfer(*, paths, stores, cwd=None, stdin=None, stdout=None, stderr=None, api_factory=ApiClient, uploader=None, sleep=time.sleep):
    """Run the upload-only Git LFS custom transfer-agent protocol.

    Parameters
    ----------
    paths : niyan.config.AppPaths
        CLI configuration and data paths.
    stores : niyan.credentials.CredentialStores
        Credential storage adapters.
    cwd : pathlib.Path, optional
        Niyān dataset checkout.
    stdin : text file-like object, optional
        Line-delimited requests from Git LFS.
    stdout : text file-like object, optional
        Line-delimited responses to Git LFS.
    stderr : text file-like object, optional
        Fatal diagnostic output.
    api_factory : callable, optional
        Niyān control API client constructor.
    uploader : DirectUploader, optional
        Direct object-storage uploader.
    sleep : callable, optional
        Retry delay dependency.

    Returns
    -------
    int
        Process exit status. Individual transfer failures still return zero.
    """

    input_stream = stdin or sys.stdin
    error_stream = stderr or sys.stderr
    writer = ProtocolWriter(stdout or sys.stdout)
    initialization = _read_message(input_stream)
    if initialization.get('event') != 'init':
        raise TransferFailure('Git LFS did not initialize the custom transfer protocol.')
    if initialization.get('operation') != 'upload':
        writer.send({'error': {'code': 2, 'message': 'The Niyān custom transfer agent supports uploads only.'}})
        return 0

    checkout = Path(cwd or Path.cwd())
    try:
        identity = load_checkout_identity(checkout)
        if identity is None:
            raise GitError('The Niyān custom transfer agent requires a configured dataset checkout.')
        credential = select_credential(host=identity.host, dataset_path=identity.dataset_path, dataset_id=identity.dataset_id, paths=paths, stores=stores, cwd=checkout)
    except NiyanCliError as error:
        writer.send({'error': {'code': 2, 'message': str(error)}})
        return 0
    transfer = UploadTransfer(identity=identity, token=credential.token, writer=writer, api_factory=api_factory, uploader=uploader, sleep=sleep)
    writer.send({})

    while True:
        try:
            request = _read_message(input_stream)
        except TransferFailure as error:
            print(f'error: {error}', file=error_stream)
            return 1
        event = request.get('event')
        if event == 'terminate':
            return 0
        oid = request.get('oid')
        if event != 'upload' or not isinstance(oid, str):
            print('error: Git LFS sent an unsupported custom transfer event.', file=error_stream)
            return 1
        try:
            writer.reset_progress(oid)
            transfer.upload(request)
        except (ApiError, OSError, TransferFailure) as error:
            writer.send({'event': 'complete', 'oid': oid, 'error': {'code': _transfer_error_code(error), 'message': _transfer_error_message(error)}})
        else:
            writer.send({'event': 'complete', 'oid': oid})


def _read_message(stream):
    """Read and validate one bounded line-delimited JSON object."""

    line = stream.readline(MAX_PROTOCOL_LINE_BYTES + 1)
    if not line:
        raise TransferFailure('Git LFS closed the custom transfer protocol unexpectedly.')
    try:
        line_size = len(line.encode('utf-8'))
    except UnicodeError as error:
        raise TransferFailure('Git LFS sent invalid transfer text.') from error
    if line_size > MAX_PROTOCOL_LINE_BYTES or not line.endswith('\n'):
        raise TransferFailure('Git LFS sent an oversized or unterminated protocol message.')
    try:
        payload = json.loads(line)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise TransferFailure('Git LFS sent invalid transfer JSON.') from error
    if not isinstance(payload, dict):
        raise TransferFailure('Git LFS transfer messages must be JSON objects.')
    return payload


def _validated_upload_request(request):
    """Validate one upload request without trusting local paths or action metadata."""

    oid = request.get('oid')
    size = request.get('size')
    path = request.get('path')
    action = request.get('action')
    if not isinstance(oid, str) or not OID_PATTERN.fullmatch(oid):
        raise TransferFailure('Git LFS supplied an invalid object identifier.')
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise TransferFailure('Git LFS supplied an invalid object size.')
    if not isinstance(path, str) or not path or '\x00' in path:
        raise TransferFailure('Git LFS supplied an invalid local object path.')
    if not isinstance(action, dict):
        raise TransferFailure('Git LFS supplied an invalid upload action.')
    return oid, size, Path(path), action


def _verify_local_object(*, path, oid, size):
    """Incrementally verify the untrusted local path against its LFS pointer."""

    digest = hashlib.sha256()
    byte_count = 0
    try:
        if path.is_symlink() or not path.is_file():
            raise TransferFailure('The local Git LFS object path is not a regular file.')
        before = path.stat()
        fingerprint = (before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns)
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                byte_count += len(chunk)
                if byte_count > size:
                    raise TransferFailure('The local Git LFS object size does not match its pointer.')
                digest.update(chunk)
    except TransferFailure:
        raise
    except OSError as error:
        raise TransferFailure('The local Git LFS object is unavailable.') from error
    if byte_count != size:
        raise TransferFailure('The local Git LFS object size does not match its pointer.')
    if digest.hexdigest() != oid:
        raise TransferFailure('The local Git LFS object checksum does not match its pointer.')
    _verify_local_fingerprint(path, fingerprint)
    return fingerprint


def _verify_local_fingerprint(path, expected):
    """Reject local object replacement or mutation during a direct transfer."""

    try:
        current = path.stat()
    except OSError as error:
        raise TransferFailure('The local Git LFS object changed during upload.') from error
    fingerprint = (current.st_dev, current.st_ino, current.st_mode, current.st_size, current.st_mtime_ns)
    if path.is_symlink() or fingerprint != expected:
        raise TransferFailure('The local Git LFS object changed during upload.')


def _validated_action(action, *, expected_size):
    """Validate one signed direct PUT action and its bounded headers."""

    href = action.get('href')
    headers = action.get('header', {})
    if action.get('method', 'PUT') != 'PUT':
        raise TransferFailure('The upload action requires an unsupported HTTP method.')
    if not isinstance(href, str):
        raise TransferFailure('The upload action is missing its signed URL.')
    parsed = urlparse(href)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise TransferFailure('The upload action contains an invalid signed URL.')
    if not isinstance(headers, dict) or len(headers) > 64 or any(not isinstance(name, str) or not isinstance(value, str) or not name or '\r' in name or '\n' in name or '\r' in value or '\n' in value for name, value in headers.items()):
        raise TransferFailure('The upload action contains invalid signed headers.')
    content_lengths = [value for name, value in headers.items() if name.lower() == 'content-length']
    if content_lengths and content_lengths != [str(expected_size)]:
        raise TransferFailure('The upload action content length does not match the requested byte range.')
    return href, headers


def _validated_session(payload, *, oid, size):
    """Validate one opaque multipart session and exact layout."""

    session_id = payload.get('session_id')
    part_size = payload.get('part_size')
    part_count = payload.get('part_count')
    if payload.get('oid') != oid or payload.get('size') != size or not isinstance(session_id, str) or not session_id or isinstance(part_size, bool) or not isinstance(part_size, int) or part_size < 1 or isinstance(part_count, bool) or not isinstance(part_count, int) or part_count < 1 or part_count > 10_000:
        raise TransferFailure('Niyān returned invalid multipart session metadata.')
    expected_parts = max(1, (size + part_size - 1) // part_size)
    if expected_parts != part_count:
        raise TransferFailure('Niyān returned an inconsistent multipart layout.')
    return session_id, part_size, part_count


def _transfer_error_code(error):
    """Return one Git LFS completion error code without exposing internals."""

    if isinstance(error, ApiError) and error.status:
        return error.status
    return 2


def _transfer_error_message(error):
    """Return a concise public transfer failure message."""

    if isinstance(error, (ApiError, TransferFailure)):
        return str(error)
    return 'The Niyān upload failed before completion.'
