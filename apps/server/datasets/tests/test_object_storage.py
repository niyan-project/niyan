from unittest import TestCase

import boto3
from botocore.client import Config
from botocore.stub import Stubber

from datasets.object_storage import CompletedPart, ObjectNotFound, ObjectStoreError, S3Configuration, S3ObjectStore


class S3ConfigurationTests(TestCase):
    """Verify provider-neutral S3 settings fail early and safely."""

    def test_normalizes_prefix_and_hides_credentials(self):
        """Normalize key prefixes without exposing credentials in diagnostics."""

        configuration = S3Configuration(
            bucket='datasets',
            key_prefix='/installation/data/',
            access_key_id='access-key',
            secret_access_key='secret-key',
            session_token='session-token',
        )

        self.assertEqual(configuration.key_prefix, 'installation/data')
        self.assertNotIn('access-key', repr(configuration))
        self.assertNotIn('secret-key', repr(configuration))
        self.assertNotIn('session-token', repr(configuration))

    def test_rejects_incomplete_or_unsupported_settings(self):
        """Reject settings that cannot be represented safely by the adapter."""

        invalid_configurations = [
            {'bucket': ''},
            {'bucket': 'invalid/name'},
            {'bucket': 'datasets', 'region_name': ' '},
            {'bucket': 'datasets', 'addressing_style': 'other'},
            {'bucket': 'datasets', 'signature_version': 's3'},
            {'bucket': 'datasets', 'access_key_id': 'access-key'},
            {'bucket': 'datasets', 'secret_access_key': 'secret-key'},
            {'bucket': 'datasets', 'session_token': 'session-token'},
            {'bucket': 'datasets', 'key_prefix': '../outside'},
            {'bucket': 'datasets', 'connect_timeout_seconds': 0},
            {'bucket': 'datasets', 'endpoint_url': 'http://storage.example.test'},
            {'bucket': 'datasets', 'endpoint_url': 'https://user:password@storage.example.test'},
            {'bucket': 'datasets', 'endpoint_url': 'https://storage.example.test/api'},
            {'bucket': 'datasets', 'endpoint_url': 'https://storage.example.test?credential=value'},
        ]

        for arguments in invalid_configurations:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    S3Configuration(**arguments)

    def test_allows_loopback_http_for_development(self):
        """Permit local development services without weakening remote endpoints."""

        configuration = S3Configuration(bucket='datasets', endpoint_url='http://127.0.0.1:9000/')

        self.assertEqual(configuration.endpoint_url, 'http://127.0.0.1:9000')


class S3ObjectStoreTests(TestCase):
    """Verify S3 orchestration without contacting a real object store."""

    def setUp(self):
        """Create an explicitly credentialed client and deterministic stubber."""

        self.configuration = S3Configuration(
            bucket='datasets',
            region_name='us-east-1',
            endpoint_url='https://storage.example.test',
            access_key_id='test-access-key',
            secret_access_key='test-secret-key',
            addressing_style='path',
            key_prefix='niyan',
        )
        self.client = boto3.client(
            's3',
            region_name=self.configuration.region_name,
            endpoint_url=self.configuration.endpoint_url,
            aws_access_key_id=self.configuration.access_key_id,
            aws_secret_access_key=self.configuration.secret_access_key,
            config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}),
        )
        self.store = S3ObjectStore(self.configuration, client=self.client)
        self.stubber = Stubber(self.client)
        self.stubber.activate()

    def tearDown(self):
        """Assert every expected provider call occurred."""

        self.stubber.assert_no_pending_responses()
        self.stubber.deactivate()

    def test_head_returns_metadata_without_object_body(self):
        """Read only metadata needed for transfer finalization."""

        self.stubber.add_response(
            'head_object',
            {
                'ContentLength': 12,
                'ETag': '"provider-etag"',
                'ChecksumSHA256': 'provider-sha256',
                'ChecksumCRC32C': 'provider-crc32c',
            },
            {'Bucket': 'datasets', 'Key': 'niyan/datasets/id/objects/oid'},
        )

        stored_object = self.store.head('datasets/id/objects/oid')

        self.assertEqual(stored_object.relative_key, 'datasets/id/objects/oid')
        self.assertEqual(stored_object.size, 12)
        self.assertEqual(stored_object.etag, '"provider-etag"')
        self.assertEqual(stored_object.checksum_sha256, 'provider-sha256')
        self.assertEqual(stored_object.checksum_crc32c, 'provider-crc32c')

    def test_head_requests_checksum_metadata_only_when_configured(self):
        """Avoid optional checksum extensions on providers that do not support them."""

        configuration = S3Configuration(
            bucket='datasets',
            region_name='us-east-1',
            endpoint_url='https://storage.example.test',
            access_key_id='test-access-key',
            secret_access_key='test-secret-key',
            addressing_style='path',
            key_prefix='niyan',
            sha256_checksums=True,
        )
        store = S3ObjectStore(configuration, client=self.client)
        self.stubber.add_response(
            'head_object',
            {'ContentLength': 12, 'ChecksumSHA256': 'provider-sha256'},
            {'Bucket': 'datasets', 'Key': 'niyan/datasets/id/objects/oid', 'ChecksumMode': 'ENABLED'},
        )

        stored_object = store.head('datasets/id/objects/oid')

        self.assertTrue(store.supports_sha256_checksums)
        self.assertEqual(stored_object.checksum_sha256, 'provider-sha256')

    def test_head_maps_missing_object_to_stable_exception(self):
        """Hide provider response details behind a stable missing-object error."""

        self.stubber.add_client_error(
            'head_object',
            service_error_code='NoSuchKey',
            service_message='secret provider detail',
            http_status_code=404,
            expected_params={'Bucket': 'datasets', 'Key': 'niyan/datasets/id/objects/missing'},
        )

        with self.assertRaisesRegex(ObjectNotFound, 'stored object is unavailable') as context:
            self.store.head('datasets/id/objects/missing')

        self.assertNotIn('secret provider detail', str(context.exception))

    def test_head_sanitizes_other_provider_failures(self):
        """Keep internal keys and provider messages out of public exceptions."""

        self.stubber.add_client_error(
            'head_object',
            service_error_code='AccessDenied',
            service_message='sensitive provider response',
            http_status_code=403,
            expected_params={'Bucket': 'datasets', 'Key': 'niyan/datasets/id/objects/oid'},
        )

        with self.assertRaisesRegex(ObjectStoreError, 'object-storage operation failed') as context:
            self.store.head('datasets/id/objects/oid')

        self.assertNotIn('sensitive provider response', str(context.exception))
        self.assertNotIn('datasets/id/objects/oid', str(context.exception))

    def test_presigns_direct_download_and_upload_actions(self):
        """Generate narrow actions while keeping all bulk bytes out of Django."""

        download = self.store.presign_download('datasets/id/objects/oid', expires_in=300)
        upload = self.store.presign_upload('datasets/id/objects/oid', size=12, expires_in=300, checksum_sha256='provider-sha256')

        self.assertEqual(download.method, 'GET')
        self.assertEqual(download.expires_in, 300)
        self.assertIn('/datasets/niyan/datasets/id/objects/oid', download.url)
        self.assertEqual(upload.method, 'PUT')
        self.assertEqual(upload.headers, {'Content-Length': '12', 'x-amz-checksum-sha256': 'provider-sha256'})
        self.assertIn('X-Amz-Signature=', upload.url)

    def test_coordinates_multipart_lifecycle(self):
        """Create, sign, complete, inspect, abort, and delete without part bytes."""

        key = 'datasets/id/objects/oid'
        provider_key = f'niyan/{key}'
        self.stubber.add_response(
            'create_multipart_upload',
            {'Bucket': 'datasets', 'Key': provider_key, 'UploadId': 'upload-id'},
            {'Bucket': 'datasets', 'Key': provider_key, 'ChecksumAlgorithm': 'CRC32C'},
        )
        self.stubber.add_response(
            'complete_multipart_upload',
            {'Bucket': 'datasets', 'Key': provider_key, 'ETag': '"complete-etag"'},
            {
                'Bucket': 'datasets',
                'Key': provider_key,
                'UploadId': 'upload-id',
                'MultipartUpload': {'Parts': [{'PartNumber': 1, 'ETag': '"part-etag"', 'ChecksumCRC32C': 'part-checksum'}]},
            },
        )
        self.stubber.add_response(
            'head_object',
            {'ContentLength': 12, 'ETag': '"complete-etag"', 'ChecksumCRC32C': 'object-checksum'},
            {'Bucket': 'datasets', 'Key': provider_key},
        )
        self.stubber.add_response('abort_multipart_upload', {}, {'Bucket': 'datasets', 'Key': provider_key, 'UploadId': 'other-upload-id'})
        self.stubber.add_response('delete_object', {}, {'Bucket': 'datasets', 'Key': provider_key})

        upload_id = self.store.initiate_multipart(key, checksum_algorithm='crc32c')
        part_action = self.store.presign_upload_part(key, upload_id=upload_id, part_number=1, expires_in=300)
        stored_object = self.store.complete_multipart(key, upload_id=upload_id, parts=[CompletedPart(part_number=1, etag='"part-etag"', checksum_crc32c='part-checksum')])
        self.store.abort_multipart(key, upload_id='other-upload-id')
        self.store.delete(key)

        self.assertEqual(upload_id, 'upload-id')
        self.assertEqual(part_action.method, 'PUT')
        self.assertIn('uploadId=upload-id', part_action.url)
        self.assertIn('partNumber=1', part_action.url)
        self.assertEqual(stored_object.size, 12)
        self.assertEqual(stored_object.checksum_crc32c, 'object-checksum')

    def test_rejects_unsafe_keys_and_invalid_actions_before_provider_calls(self):
        """Reject caller-controlled traversal and invalid transfer parameters."""

        for key in ['', '/absolute', '../outside', 'nested//object', 'nested\\object', 'object\nheader']:
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    self.store.head(key)

        invalid_actions = [
            lambda: self.store.presign_download('object', expires_in=0),
            lambda: self.store.presign_download('object', expires_in=3601),
            lambda: self.store.presign_upload('object', size=-1),
            lambda: self.store.initiate_multipart('object', checksum_algorithm='md5'),
            lambda: self.store.presign_upload_part('object', upload_id='', part_number=1),
            lambda: self.store.presign_upload_part('object', upload_id='upload-id', part_number=0),
            lambda: self.store.complete_multipart('object', upload_id='upload-id', parts=[]),
            lambda: self.store.complete_multipart(
                'object',
                upload_id='upload-id',
                parts=[CompletedPart(part_number=2, etag='"two"'), CompletedPart(part_number=1, etag='"one"')],
            ),
        ]
        for action in invalid_actions:
            with self.subTest(action=action):
                with self.assertRaises(ValueError):
                    action()

    def test_completed_part_validates_number_and_etag(self):
        """Reject malformed provider completion metadata."""

        with self.assertRaises(ValueError):
            CompletedPart(part_number=0, etag='"etag"')
        with self.assertRaises(ValueError):
            CompletedPart(part_number=1, etag='')
