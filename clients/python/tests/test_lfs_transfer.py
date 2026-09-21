import hashlib
import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from niyan.config import AppPaths, CheckoutIdentity
from niyan.lfs_transfer import DirectUploader, ProtocolWriter, RetryableTransferFailure, TransferFailure, UploadTransfer, run_lfs_transfer


DATASET_ID = '22222222-2222-2222-2222-222222222222'
DATASET_PATH = 'researcher/images'
HOST = 'https://niyan.example'
OBJECT_CONTENT = b'0123456789'
OID = hashlib.sha256(OBJECT_CONTENT).hexdigest()


class RecordingUploader:
    """Record exact ranges and optionally interrupt one first attempt."""

    def __init__(self, *, retry_offset=None, terminal=False, always_retryable=False):
        """Configure deterministic transient or terminal behavior."""

        self.retry_offset = retry_offset
        self.terminal = terminal
        self.always_retryable = always_retryable
        self.calls = []
        self.attempts = {}
        self.lock = threading.Lock()

    def put(self, *, action, path, offset, size):
        """Return provider metadata after recording the requested file range."""

        with self.lock:
            self.calls.append((action['href'], Path(path), offset, size))
            self.attempts[offset] = self.attempts.get(offset, 0) + 1
            attempt = self.attempts[offset]
        if self.terminal:
            raise TransferFailure('terminal upload failure')
        if self.always_retryable:
            raise RetryableTransferFailure('persistent storage outage')
        if self.retry_offset == offset and attempt == 1:
            raise RetryableTransferFailure('interrupted part')
        return {'etag': f'"etag-{offset}"', 'checksum_crc32c': f'checksum-{offset}'}


class MultipartApi:
    """Implement the public multipart-control contract without handling bytes."""

    def __init__(self, *, size=10, part_size=4):
        """Create one deterministic opaque multipart session."""

        self.size = size
        self.part_size = part_size
        self.part_count = (size + part_size - 1) // part_size
        self.session_id = '11111111-1111-1111-1111-111111111111'
        self.calls = []
        self.completed_parts = None

    def factory(self, host, token=None):
        """Validate the selected checkout host and secret without exposing it."""

        if host != HOST or token != 'niyan_test_secret':
            raise AssertionError('unexpected control credential')
        return self

    def request(self, method, url, *, payload=None, accepted_statuses=None):
        """Return bounded public control metadata for one request."""

        self.calls.append((method, url, payload))
        if method == 'POST' and url.endswith('/multipart'):
            return 200, {
                'session_id': self.session_id,
                'oid': OID,
                'size': self.size,
                'part_size': self.part_size,
                'part_count': self.part_count,
                'expires_at': '2030-01-01T00:00:00Z',
            }
        if method == 'POST' and '/parts/' in url:
            part_number = int(url.rsplit('/', 1)[-1])
            return 200, {'method': 'PUT', 'href': f'https://storage.example.test/part/{part_number}', 'header': {'Content-Length': str(payload['size'])}, 'expires_in': 300}
        if method == 'POST' and url.endswith('/complete'):
            self.completed_parts = payload['parts']
            return 200, {'session_id': self.session_id, 'oid': OID, 'size': self.size, 'state': 'completed'}
        if method == 'DELETE':
            return 200, {'session_id': self.session_id, 'oid': OID, 'size': self.size, 'state': 'aborted'}
        raise AssertionError(f'unexpected control request: {method} {url}')


class CustomTransferProtocolTests(unittest.TestCase):
    """Exercise the Git LFS line-delimited JSON process boundary."""

    def setUp(self):
        """Create isolated paths and one checkout identity."""

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.paths = AppPaths(config_home=self.root / 'config', data_home=self.root / 'data')
        self.identity = CheckoutIdentity(host=HOST, dataset_id=DATASET_ID, dataset_path=DATASET_PATH)

    def tearDown(self):
        """Remove local transfer fixtures."""

        self.temporary_directory.cleanup()

    def test_protocol_acknowledges_upload_progress_and_completion(self):
        """Speak the official init, upload, progress, complete, terminate sequence."""

        content = OBJECT_CONTENT
        object_path = self.root / 'object'
        object_path.write_bytes(content)
        messages = [
            {'event': 'init', 'operation': 'upload', 'remote': 'origin', 'concurrent': False, 'concurrenttransfers': 8},
            {'event': 'upload', 'oid': OID, 'size': len(content), 'path': str(object_path), 'action': {'href': 'https://storage.example.test/object', 'header': {'Content-Length': str(len(content))}}},
            {'event': 'terminate'},
        ]
        protocol_input = io.StringIO(''.join(f'{json.dumps(message)}\n' for message in messages))
        protocol_output = io.StringIO()
        uploader = RecordingUploader()

        with patch('niyan.lfs_transfer.load_checkout_identity', return_value=self.identity), patch('niyan.lfs_transfer.select_credential', return_value=SimpleNamespace(token='niyan_test_secret')):
            status = run_lfs_transfer(paths=self.paths, stores=object(), cwd=self.root, stdin=protocol_input, stdout=protocol_output, stderr=io.StringIO(), uploader=uploader)

        responses = [json.loads(line) for line in protocol_output.getvalue().splitlines()]
        self.assertEqual(status, 0)
        self.assertEqual(responses[0], {})
        self.assertEqual(responses[1], {'event': 'progress', 'oid': OID, 'bytesSoFar': len(content), 'bytesSinceLast': len(content)})
        self.assertEqual(responses[2], {'event': 'complete', 'oid': OID})
        self.assertNotIn('niyan_test_secret', protocol_output.getvalue())
        self.assertEqual(uploader.calls[0][2:], (0, len(content)))

    def test_protocol_rejects_download_mode_during_initialization(self):
        """Keep downloads on Git LFS's standard basic adapter."""

        protocol_input = io.StringIO(json.dumps({'event': 'init', 'operation': 'download'}) + '\n')
        protocol_output = io.StringIO()

        status = run_lfs_transfer(paths=self.paths, stores=object(), cwd=self.root, stdin=protocol_input, stdout=protocol_output, stderr=io.StringIO())

        self.assertEqual(status, 0)
        self.assertIn('supports uploads only', protocol_output.getvalue())

    def test_initialization_failure_uses_the_protocol_error_response(self):
        """Report missing checkout state without corrupting stdout with diagnostics."""

        protocol_input = io.StringIO(json.dumps({'event': 'init', 'operation': 'upload'}) + '\n')
        protocol_output = io.StringIO()

        with patch('niyan.lfs_transfer.load_checkout_identity', return_value=None):
            status = run_lfs_transfer(paths=self.paths, stores=object(), cwd=self.root, stdin=protocol_input, stdout=protocol_output, stderr=io.StringIO())

        self.assertEqual(status, 0)
        response = json.loads(protocol_output.getvalue())
        self.assertIn('error', response)
        self.assertIn('configured dataset checkout', response['error']['message'])

    def test_corrupt_local_object_is_a_per_transfer_error(self):
        """Keep an OID mismatch off the network and continue to termination."""

        object_path = self.root / 'corrupt-object'
        object_path.write_bytes(OBJECT_CONTENT)
        wrong_oid = 'b' * 64
        messages = [
            {'event': 'init', 'operation': 'upload'},
            {'event': 'upload', 'oid': wrong_oid, 'size': len(OBJECT_CONTENT), 'path': str(object_path), 'action': {'href': 'https://storage.example.test/object'}},
            {'event': 'terminate'},
        ]
        protocol_input = io.StringIO(''.join(f'{json.dumps(message)}\n' for message in messages))
        protocol_output = io.StringIO()
        uploader = RecordingUploader()

        with patch('niyan.lfs_transfer.load_checkout_identity', return_value=self.identity), patch('niyan.lfs_transfer.select_credential', return_value=SimpleNamespace(token='niyan_test_secret')):
            status = run_lfs_transfer(paths=self.paths, stores=object(), cwd=self.root, stdin=protocol_input, stdout=protocol_output, stderr=io.StringIO(), uploader=uploader)

        responses = [json.loads(line) for line in protocol_output.getvalue().splitlines()]
        self.assertEqual(status, 0)
        self.assertIn('checksum does not match', responses[1]['error']['message'])
        self.assertEqual(uploader.calls, [])

    def test_multipart_upload_retries_one_part_completes_and_reports_exact_progress(self):
        """Refresh a part action after interruption and send ordered provider metadata."""

        object_path = self.root / 'large-object'
        object_path.write_bytes(OBJECT_CONTENT)
        api = MultipartApi()
        uploader = RecordingUploader(retry_offset=4)
        output = io.StringIO()
        transfer = UploadTransfer(identity=self.identity, token='niyan_test_secret', writer=ProtocolWriter(output), api_factory=api.factory, uploader=uploader, sleep=lambda _: None)
        initiation_url = f'{HOST}/api/v1/datasets/{DATASET_ID}/lfs/objects/{OID}/multipart'

        transfer.upload({'event': 'upload', 'oid': OID, 'size': 10, 'path': str(object_path), 'action': {'href': initiation_url}})

        self.assertEqual(uploader.attempts, {0: 1, 4: 2, 8: 1})
        self.assertEqual([part['part_number'] for part in api.completed_parts], [1, 2, 3])
        self.assertEqual([part['etag'] for part in api.completed_parts], ['"etag-0"', '"etag-4"', '"etag-8"'])
        progress = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(progress[-1]['bytesSoFar'], 10)
        self.assertEqual(sum(message['bytesSinceLast'] for message in progress), 10)
        self.assertFalse(any(method == 'DELETE' for method, _, _ in api.calls))

    def test_terminal_multipart_failure_requests_abort(self):
        """Abort the opaque server session when a part cannot succeed."""

        object_path = self.root / 'large-object'
        object_path.write_bytes(OBJECT_CONTENT)
        api = MultipartApi()
        transfer = UploadTransfer(identity=self.identity, token='niyan_test_secret', writer=ProtocolWriter(io.StringIO()), api_factory=api.factory, uploader=RecordingUploader(terminal=True), sleep=lambda _: None)
        initiation_url = f'{HOST}/api/v1/datasets/{DATASET_ID}/lfs/objects/{OID}/multipart'

        with self.assertRaisesRegex(TransferFailure, 'terminal upload failure'):
            transfer.upload({'event': 'upload', 'oid': OID, 'size': 10, 'path': str(object_path), 'action': {'href': initiation_url}})

        self.assertTrue(any(method == 'DELETE' and url.endswith(api.session_id) for method, url, _ in api.calls))

    def test_retryable_multipart_failure_stops_at_budget_and_aborts(self):
        """Bound transient retries and retire the server session after exhaustion."""

        object_path = self.root / 'large-object'
        object_path.write_bytes(OBJECT_CONTENT)
        api = MultipartApi()
        uploader = RecordingUploader(always_retryable=True)
        transfer = UploadTransfer(identity=self.identity, token='niyan_test_secret', writer=ProtocolWriter(io.StringIO()), api_factory=api.factory, uploader=uploader, sleep=lambda _: None, workers=1, attempts=3)
        initiation_url = f'{HOST}/api/v1/datasets/{DATASET_ID}/lfs/objects/{OID}/multipart'

        with self.assertRaisesRegex(RetryableTransferFailure, 'persistent storage outage'):
            transfer.upload({'event': 'upload', 'oid': OID, 'size': 10, 'path': str(object_path), 'action': {'href': initiation_url}})

        self.assertEqual(uploader.attempts, {0: 3, 4: 3, 8: 3})
        self.assertIsNone(api.completed_parts)
        self.assertTrue(any(method == 'DELETE' and url.endswith(api.session_id) for method, url, _ in api.calls))

    def test_invalid_control_url_never_receives_object_bytes(self):
        """Refuse to reinterpret a malformed Niyān API action as direct storage."""

        object_path = self.root / 'object'
        object_path.write_bytes(OBJECT_CONTENT)
        uploader = RecordingUploader()
        transfer = UploadTransfer(identity=self.identity, token='niyan_test_secret', writer=ProtocolWriter(io.StringIO()), uploader=uploader)

        with self.assertRaisesRegex(TransferFailure, 'invalid multipart control URL'):
            transfer.upload({'event': 'upload', 'oid': OID, 'size': len(OBJECT_CONTENT), 'path': str(object_path), 'action': {'href': f'{HOST}/api/v1/datasets/wrong/lfs'}})

        self.assertEqual(uploader.calls, [])


class DirectUploaderTests(unittest.TestCase):
    """Verify byte traffic goes straight to signed storage URLs."""

    def test_streams_only_the_requested_range_and_returns_provider_metadata(self):
        """Send an exact content-length range without any Django data proxy."""

        received = {}

        class Handler(BaseHTTPRequestHandler):
            """Capture one signed PUT request."""

            def do_PUT(self):
                """Read the declared body and return S3-style metadata."""

                size = int(self.headers['Content-Length'])
                received['path'] = self.path
                received['body'] = self.rfile.read(size)
                received['header'] = self.headers['X-Test-Signature']
                self.send_response(200)
                self.send_header('ETag', '"part-etag"')
                self.send_header('x-amz-checksum-crc32c', 'part-checksum')
                self.end_headers()

            def log_message(self, format, *args):
                """Suppress test-server diagnostics."""

                return

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'object'
                path.write_bytes(b'abcdefghij')
                result = DirectUploader().put(
                    action={'method': 'PUT', 'href': f'http://127.0.0.1:{server.server_port}/signed-part?token=opaque', 'header': {'Content-Length': '4', 'X-Test-Signature': 'signed'}},
                    path=path,
                    offset=3,
                    size=4,
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(received, {'path': '/signed-part?token=opaque', 'body': b'defg', 'header': 'signed'})
        self.assertEqual(result['etag'], '"part-etag"')
        self.assertEqual(result['checksum_crc32c'], 'part-checksum')


if __name__ == '__main__':
    unittest.main()
