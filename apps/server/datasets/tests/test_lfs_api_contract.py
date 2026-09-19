from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase
from django.utils import timezone

from datasets.lfs_api import MultipartCompleteInput, MultipartInitiateInput, MultipartPartInput, abort_multipart_endpoint, complete_multipart_endpoint, initiate_multipart_endpoint, multipart_part_endpoint
from datasets.lfs_transfers import LfsIntegrityError, LfsMultipartExpired, LfsTransferUnavailable
from datasets.models import LfsMultipartUpload
from datasets.object_storage import ObjectStoreError


class LfsApiContractTests(SimpleTestCase):
    """Exercise multipart control response mapping independently of service tests."""

    dataset_id = uuid4()
    session_id = uuid4()
    oid = 'a' * 64

    def setUp(self):
        """Create stable request, object, and multipart session doubles."""

        self.request = SimpleNamespace(auth=object())
        self.lfs_object = SimpleNamespace(oid=self.oid, size=11)
        self.session = SimpleNamespace(id=self.session_id, lfs_object=self.lfs_object, part_size=5, expected_part_count=3, expires_at=timezone.now() + timedelta(hours=1), state=LfsMultipartUpload.State.ACTIVE)

    def assert_status(self, response, expected_status, expected_code):
        """Assert one django-ninja status response."""

        self.assertEqual(response.status_code, expected_status)
        self.assertEqual(response.value['code'], expected_code)

    def test_initiation_serializes_session_and_maps_all_failures(self):
        """Validate object identity before translating storage outcomes."""

        with patch('datasets.lfs_api._get_writable_lfs_object', return_value=None):
            self.assert_status(initiate_multipart_endpoint(self.request, self.dataset_id, self.oid, MultipartInitiateInput(size=11)), 404, 'lfs_object_not_found')
        with patch('datasets.lfs_api._get_writable_lfs_object', return_value=self.lfs_object):
            self.assert_status(initiate_multipart_endpoint(self.request, self.dataset_id, self.oid, MultipartInitiateInput(size=12)), 422, 'lfs_size_mismatch')
        with patch('datasets.lfs_api._get_writable_lfs_object', return_value=self.lfs_object), patch('datasets.lfs_api.initiate_multipart_upload', return_value=self.session):
            response = initiate_multipart_endpoint(self.request, self.dataset_id, self.oid, MultipartInitiateInput(size=11))
        self.assertEqual(response['session_id'], self.session_id)
        self.assertEqual(response['part_count'], 3)

        mappings = [
            (LfsIntegrityError('invalid'), 422, 'lfs_object_invalid'),
            (LfsTransferUnavailable('unavailable'), 409, 'multipart_unavailable'),
            (ObjectStoreError('provider'), 503, 'object_storage_unavailable'),
        ]
        for error, status, code in mappings:
            with self.subTest(error=type(error).__name__), patch('datasets.lfs_api._get_writable_lfs_object', return_value=self.lfs_object), patch('datasets.lfs_api.initiate_multipart_upload', side_effect=error):
                self.assert_status(initiate_multipart_endpoint(self.request, self.dataset_id, self.oid, MultipartInitiateInput(size=11)), status, code)

    def test_part_action_serializes_upload_and_maps_all_failures(self):
        """Issue exact-size signed actions without exposing provider state."""

        payload = MultipartPartInput(size=5)
        with patch('datasets.lfs_api._get_writable_session', return_value=None):
            self.assert_status(multipart_part_endpoint(self.request, self.dataset_id, self.oid, self.session_id, 1, payload), 404, 'multipart_not_found')
        action = SimpleNamespace(method='PUT', url='https://objects.example/part', headers={'Content-Length': '5'}, expires_in=300)
        with patch('datasets.lfs_api._get_writable_session', return_value=self.session), patch('datasets.lfs_api.issue_multipart_part_action', return_value=action):
            response = multipart_part_endpoint(self.request, self.dataset_id, self.oid, self.session_id, 1, payload)
        self.assertEqual(response['href'], action.url)

        mappings = [
            (LfsMultipartExpired('expired'), 409, 'multipart_expired'),
            (LfsIntegrityError('invalid'), 422, 'multipart_part_invalid'),
            (ValueError('invalid'), 422, 'multipart_part_invalid'),
            (LfsTransferUnavailable('unavailable'), 409, 'multipart_unavailable'),
            (ObjectStoreError('provider'), 503, 'object_storage_unavailable'),
        ]
        for error, status, code in mappings:
            with self.subTest(error=type(error).__name__), patch('datasets.lfs_api._get_writable_session', return_value=self.session), patch('datasets.lfs_api.issue_multipart_part_action', side_effect=error):
                self.assert_status(multipart_part_endpoint(self.request, self.dataset_id, self.oid, self.session_id, 1, payload), status, code)

    def test_completion_builds_provider_parts_and_maps_all_failures(self):
        """Translate public part metadata to the storage-neutral service shape."""

        payload = MultipartCompleteInput(parts=[{'part_number': 1, 'etag': '"etag"', 'checksum_sha256': 'checksum'}])
        with patch('datasets.lfs_api._get_writable_session', return_value=None):
            self.assert_status(complete_multipart_endpoint(self.request, self.dataset_id, self.oid, self.session_id, payload), 404, 'multipart_not_found')
        completed = SimpleNamespace(id=self.session_id, lfs_object=self.lfs_object, state=LfsMultipartUpload.State.COMPLETED)
        with patch('datasets.lfs_api._get_writable_session', return_value=self.session), patch('datasets.lfs_api.complete_multipart_upload', return_value=completed) as complete:
            response = complete_multipart_endpoint(self.request, self.dataset_id, self.oid, self.session_id, payload)
        self.assertEqual(response['state'], LfsMultipartUpload.State.COMPLETED)
        self.assertEqual(complete.call_args.kwargs['parts'][0].checksum_sha256, 'checksum')

        mappings = [
            (LfsMultipartExpired('expired'), 409, 'multipart_expired'),
            (LfsIntegrityError('invalid'), 422, 'multipart_parts_invalid'),
            (ValueError('invalid'), 422, 'multipart_parts_invalid'),
            (LfsTransferUnavailable('unavailable'), 409, 'multipart_unavailable'),
            (ObjectStoreError('provider'), 503, 'object_storage_unavailable'),
        ]
        for error, status, code in mappings:
            with self.subTest(error=type(error).__name__), patch('datasets.lfs_api._get_writable_session', return_value=self.session), patch('datasets.lfs_api.complete_multipart_upload', side_effect=error):
                self.assert_status(complete_multipart_endpoint(self.request, self.dataset_id, self.oid, self.session_id, payload), status, code)

    def test_abort_is_public_idempotent_and_maps_failures(self):
        """Return stable terminal state and sanitized provider failures."""

        with patch('datasets.lfs_api._get_writable_session', return_value=None):
            self.assert_status(abort_multipart_endpoint(self.request, self.dataset_id, self.oid, self.session_id), 404, 'multipart_not_found')
        aborted = SimpleNamespace(id=self.session_id, lfs_object=self.lfs_object, state=LfsMultipartUpload.State.ABORTED)
        with patch('datasets.lfs_api._get_writable_session', return_value=self.session), patch('datasets.lfs_api.abort_multipart_upload', return_value=aborted):
            response = abort_multipart_endpoint(self.request, self.dataset_id, self.oid, self.session_id)
        self.assertEqual(response['state'], LfsMultipartUpload.State.ABORTED)

        for error, status, code in ((LfsTransferUnavailable('unavailable'), 409, 'multipart_unavailable'), (ObjectStoreError('provider'), 503, 'object_storage_unavailable')):
            with self.subTest(error=type(error).__name__), patch('datasets.lfs_api._get_writable_session', return_value=self.session), patch('datasets.lfs_api.abort_multipart_upload', side_effect=error):
                self.assert_status(abort_multipart_endpoint(self.request, self.dataset_id, self.oid, self.session_id), status, code)


if __name__ == '__main__':
    unittest.main()
