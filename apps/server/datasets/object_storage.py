from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError


class ObjectStoreError(RuntimeError):
    """Report an object-store failure without exposing provider internals."""


class ObjectNotFound(ObjectStoreError):
    """Report that the requested internal object key does not exist."""


@dataclass(frozen=True)
class S3Configuration:
    """Hold validated, provider-neutral S3 client configuration.

    Credentials are excluded from the dataclass representation so settings and
    diagnostics cannot reveal them accidentally.
    """

    bucket: str
    region_name: str = 'us-east-1'
    endpoint_url: str | None = None
    access_key_id: str | None = field(default=None, repr=False)
    secret_access_key: str | None = field(default=None, repr=False)
    session_token: str | None = field(default=None, repr=False)
    addressing_style: str = 'auto'
    signature_version: str = 's3v4'
    key_prefix: str = 'niyan'
    sha256_checksums: bool = False
    verify_tls: bool = True
    connect_timeout_seconds: int = 5
    read_timeout_seconds: int = 30
    max_attempts: int = 3

    def __post_init__(self):
        """Reject incomplete credentials and unsafe or unsupported settings.

        Raises
        ------
        ValueError
            If a setting cannot be used safely by the v1 S3 adapter.
        """

        if not self.bucket or '/' in self.bucket or any(character.isspace() for character in self.bucket):
            raise ValueError('The S3 bucket must be a non-empty bucket name.')
        if not self.region_name.strip():
            raise ValueError('The S3 region must not be empty.')
        if self.addressing_style not in {'auto', 'path', 'virtual'}:
            raise ValueError('The S3 addressing style must be auto, path, or virtual.')
        if self.signature_version != 's3v4':
            raise ValueError('Niyān v1 supports only S3 Signature Version 4.')
        if bool(self.access_key_id) != bool(self.secret_access_key):
            raise ValueError('The S3 access key and secret key must be supplied together.')
        if self.session_token and not self.access_key_id:
            raise ValueError('An S3 session token requires an explicit access key and secret key.')
        if self.connect_timeout_seconds < 1 or self.read_timeout_seconds < 1 or self.max_attempts < 1:
            raise ValueError('S3 timeouts and retry attempts must be positive.')

        normalized_prefix = self.key_prefix.strip('/')
        if not normalized_prefix or any(part in {'', '.', '..'} for part in normalized_prefix.split('/')):
            raise ValueError('The S3 key prefix must contain safe, non-empty path segments.')
        object.__setattr__(self, 'key_prefix', normalized_prefix)

        if self.endpoint_url:
            parsed_endpoint = urlsplit(self.endpoint_url)
            if parsed_endpoint.scheme not in {'http', 'https'} or not parsed_endpoint.hostname or parsed_endpoint.username or parsed_endpoint.password or parsed_endpoint.path not in {'', '/'} or parsed_endpoint.query or parsed_endpoint.fragment:
                raise ValueError('The S3 endpoint must be an HTTP(S) origin without credentials, query, or fragment.')
            loopback_hosts = {'localhost', '127.0.0.1', '::1'}
            if parsed_endpoint.scheme != 'https' and parsed_endpoint.hostname not in loopback_hosts:
                raise ValueError('The S3 endpoint must use HTTPS unless it is a loopback development service.')
            object.__setattr__(self, 'endpoint_url', self.endpoint_url.rstrip('/'))


@dataclass(frozen=True)
class PresignedAction:
    """Describe one short-lived direct object-store HTTP action."""

    method: str
    url: str = field(repr=False)
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    expires_in: int = 900


@dataclass(frozen=True)
class StoredObject:
    """Describe object metadata returned without reading object bytes."""

    relative_key: str
    size: int
    etag: str | None = None
    checksum_sha256: str | None = None
    checksum_crc32c: str | None = None
    checksum_crc32: str | None = None


@dataclass(frozen=True)
class CompletedPart:
    """Describe one uploaded multipart part supplied to S3 completion."""

    part_number: int
    etag: str
    checksum_sha256: str | None = None
    checksum_crc32c: str | None = None
    checksum_crc32: str | None = None

    def __post_init__(self):
        """Validate the provider completion values before sending them.

        Raises
        ------
        ValueError
            If the part number or ETag is invalid.
        """

        if self.part_number < 1 or self.part_number > 10_000:
            raise ValueError('An S3 multipart part number must be between 1 and 10000.')
        if not self.etag or len(self.etag) > 1024 or any(character in self.etag for character in {'\r', '\n'}):
            raise ValueError('An S3 multipart part requires an ETag.')


class ObjectStore(Protocol):
    """Define the metadata-only object-store boundary used by domain services."""

    @property
    def supports_sha256_checksums(self) -> bool:
        """Return whether uploads bind and expose provider-validated SHA-256."""

    def head(self, relative_key: str) -> StoredObject:
        """Return metadata for one internal relative object key."""

    def presign_download(self, relative_key: str, *, expires_in: int = 900) -> PresignedAction:
        """Authorize one direct download without proxying its bytes."""

    def presign_upload(self, relative_key: str, *, size: int, expires_in: int = 900, checksum_sha256: str | None = None) -> PresignedAction:
        """Authorize one direct single-request upload."""

    def initiate_multipart(self, relative_key: str, *, checksum_algorithm: str | None = None) -> str:
        """Start a provider multipart upload and return its opaque identifier."""

    def presign_upload_part(self, relative_key: str, *, upload_id: str, part_number: int, size: int, expires_in: int = 900, checksum_sha256: str | None = None) -> PresignedAction:
        """Authorize one direct multipart part upload."""

    def complete_multipart(self, relative_key: str, *, upload_id: str, parts: list[CompletedPart]) -> StoredObject:
        """Complete a multipart upload using provider-returned part metadata."""

    def abort_multipart(self, relative_key: str, *, upload_id: str) -> None:
        """Abort an incomplete multipart upload."""

    def delete(self, relative_key: str) -> None:
        """Delete one stored object without reading it."""


class S3ObjectStore:
    """Implement the Niyān object-store boundary with the standard S3 API."""

    _CHECKSUM_ALGORITHMS = {'CRC32', 'CRC32C', 'SHA256'}

    def __init__(self, configuration: S3Configuration, *, client=None):
        """Initialize an S3 adapter without contacting object storage.

        Parameters
        ----------
        configuration : S3Configuration
            Validated endpoint, signing, credential, and retry settings.
        client : botocore.client.BaseClient, optional
            Explicit client used by deterministic tests.
        """

        self.configuration = configuration
        self.client = client if client is not None else self._create_client()

    @property
    def supports_sha256_checksums(self):
        """Return the explicitly configured provider checksum capability."""

        return self.configuration.sha256_checksums

    def _create_client(self):
        """Create a boto3 S3 client using explicit or standard credentials."""

        session_arguments = {'region_name': self.configuration.region_name}
        if self.configuration.access_key_id:
            session_arguments.update(
                aws_access_key_id=self.configuration.access_key_id,
                aws_secret_access_key=self.configuration.secret_access_key,
                aws_session_token=self.configuration.session_token,
            )
        session = boto3.session.Session(**session_arguments)
        client_configuration = Config(
            signature_version=self.configuration.signature_version,
            s3={'addressing_style': self.configuration.addressing_style},
            connect_timeout=self.configuration.connect_timeout_seconds,
            read_timeout=self.configuration.read_timeout_seconds,
            retries={'max_attempts': self.configuration.max_attempts, 'mode': 'standard'},
        )
        return session.client(
            's3',
            endpoint_url=self.configuration.endpoint_url,
            verify=self.configuration.verify_tls,
            config=client_configuration,
        )

    def _key(self, relative_key):
        """Qualify and validate an internal key beneath the installation prefix.

        Parameters
        ----------
        relative_key : str
            Trusted application-relative key, normally containing a dataset UUID.

        Returns
        -------
        str
            S3 key beneath the configured installation prefix.

        Raises
        ------
        ValueError
            If the key is absolute, empty, or contains unsafe path segments.
        """

        if not relative_key or relative_key.startswith('/') or '\\' in relative_key or any(character in relative_key for character in {'\x00', '\r', '\n'}):
            raise ValueError('Object-store keys must be safe relative keys.')
        parts = relative_key.split('/')
        if any(part in {'', '.', '..'} for part in parts):
            raise ValueError('Object-store keys must contain safe, non-empty path segments.')
        return f'{self.configuration.key_prefix}/{relative_key}'

    def _expires(self, expires_in):
        """Validate one signed-action lifetime in seconds."""

        if expires_in < 1 or expires_in > 3600:
            raise ValueError('A signed action lifetime must be between 1 and 3600 seconds.')
        return expires_in

    def _call(self, operation, **parameters):
        """Call S3 while translating provider failures to stable exceptions."""

        try:
            return getattr(self.client, operation)(**parameters)
        except ClientError as error:
            error_code = str(error.response.get('Error', {}).get('Code', ''))
            if error_code in {'404', 'NoSuchKey', 'NoSuchUpload', 'NotFound'}:
                raise ObjectNotFound('The stored object is unavailable.') from error
            raise ObjectStoreError('The object-storage operation failed.') from error
        except BotoCoreError as error:
            raise ObjectStoreError('The object-storage operation failed.') from error

    def head(self, relative_key):
        """Return provider metadata without downloading object content.

        Parameters
        ----------
        relative_key : str
            Internal key relative to the installation prefix.

        Returns
        -------
        StoredObject
            Size, ETag, and provider checksum metadata when available.
        """

        parameters = {'Bucket': self.configuration.bucket, 'Key': self._key(relative_key)}
        if self.supports_sha256_checksums:
            parameters['ChecksumMode'] = 'ENABLED'
        response = self._call('head_object', **parameters)
        return StoredObject(
            relative_key=relative_key,
            size=response['ContentLength'],
            etag=response.get('ETag'),
            checksum_sha256=response.get('ChecksumSHA256'),
            checksum_crc32c=response.get('ChecksumCRC32C'),
            checksum_crc32=response.get('ChecksumCRC32'),
        )

    def presign_download(self, relative_key, *, expires_in=900):
        """Return a short-lived direct GET action for one object."""

        return self._presign('get_object', 'GET', relative_key, expires_in=expires_in)

    def presign_upload(self, relative_key, *, size, expires_in=900, checksum_sha256=None):
        """Return a short-lived direct PUT action for one complete object."""

        if size < 0:
            raise ValueError('An upload size must not be negative.')
        parameters = {'ContentLength': size}
        headers = {'Content-Length': str(size)}
        if checksum_sha256:
            parameters['ChecksumSHA256'] = checksum_sha256
            headers['x-amz-checksum-sha256'] = checksum_sha256
        return self._presign('put_object', 'PUT', relative_key, expires_in=expires_in, parameters=parameters, headers=headers)

    def _presign(self, operation, method, relative_key, *, expires_in, parameters=None, headers=None):
        """Generate one signed action without making a network request."""

        expires_in = self._expires(expires_in)
        request_parameters = {'Bucket': self.configuration.bucket, 'Key': self._key(relative_key)}
        request_parameters.update(parameters or {})
        try:
            url = self.client.generate_presigned_url(operation, Params=request_parameters, ExpiresIn=expires_in, HttpMethod=method)
        except (BotoCoreError, ClientError) as error:
            raise ObjectStoreError('The object-storage action could not be signed.') from error
        return PresignedAction(method=method, url=url, headers=headers or {}, expires_in=expires_in)

    def initiate_multipart(self, relative_key, *, checksum_algorithm=None):
        """Start a multipart upload without handling any part bytes."""

        parameters = {'Bucket': self.configuration.bucket, 'Key': self._key(relative_key)}
        if checksum_algorithm:
            normalized_algorithm = checksum_algorithm.upper()
            if normalized_algorithm not in self._CHECKSUM_ALGORITHMS:
                raise ValueError('The multipart checksum algorithm is unsupported.')
            parameters['ChecksumAlgorithm'] = normalized_algorithm
        response = self._call('create_multipart_upload', **parameters)
        upload_id = response.get('UploadId')
        if not upload_id:
            raise ObjectStoreError('Object storage did not create a multipart upload.')
        return upload_id

    def presign_upload_part(self, relative_key, *, upload_id, part_number, size, expires_in=900, checksum_sha256=None):
        """Return a short-lived direct PUT action for one multipart part."""

        if not upload_id:
            raise ValueError('A multipart upload identifier is required.')
        if part_number < 1 or part_number > 10_000:
            raise ValueError('An S3 multipart part number must be between 1 and 10000.')
        if size < 1:
            raise ValueError('An S3 multipart part size must be positive.')
        parameters = {'UploadId': upload_id, 'PartNumber': part_number, 'ContentLength': size}
        headers = {'Content-Length': str(size)}
        if checksum_sha256:
            parameters['ChecksumSHA256'] = checksum_sha256
            headers['x-amz-checksum-sha256'] = checksum_sha256
        return self._presign(
            'upload_part',
            'PUT',
            relative_key,
            expires_in=expires_in,
            parameters=parameters,
            headers=headers,
        )

    def complete_multipart(self, relative_key, *, upload_id, parts):
        """Complete a multipart upload and return the resulting metadata."""

        if not upload_id or not parts:
            raise ValueError('Multipart completion requires an upload identifier and at least one part.')
        normalized_parts = []
        previous_part_number = 0
        for part in parts:
            if part.part_number <= previous_part_number:
                raise ValueError('Multipart parts must be ordered by increasing part number.')
            previous_part_number = part.part_number
            normalized_part = {'PartNumber': part.part_number, 'ETag': part.etag}
            if part.checksum_sha256:
                normalized_part['ChecksumSHA256'] = part.checksum_sha256
            if part.checksum_crc32c:
                normalized_part['ChecksumCRC32C'] = part.checksum_crc32c
            if part.checksum_crc32:
                normalized_part['ChecksumCRC32'] = part.checksum_crc32
            normalized_parts.append(normalized_part)
        self._call(
            'complete_multipart_upload',
            Bucket=self.configuration.bucket,
            Key=self._key(relative_key),
            UploadId=upload_id,
            MultipartUpload={'Parts': normalized_parts},
        )
        return self.head(relative_key)

    def abort_multipart(self, relative_key, *, upload_id):
        """Abort one provider multipart upload."""

        if not upload_id:
            raise ValueError('A multipart upload identifier is required.')
        self._call('abort_multipart_upload', Bucket=self.configuration.bucket, Key=self._key(relative_key), UploadId=upload_id)

    def delete(self, relative_key):
        """Delete one object without downloading its bytes."""

        self._call('delete_object', Bucket=self.configuration.bucket, Key=self._key(relative_key))
