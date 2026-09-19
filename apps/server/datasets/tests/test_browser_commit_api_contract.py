from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase, override_settings

from datasets.browser_commit_api import DraftCommitInput, DraftCreateInput, LfsStageInput, complete_lfs_file_endpoint, create_draft_endpoint, discard_draft_endpoint, get_draft_endpoint, list_drafts_endpoint, publish_draft_endpoint, stage_delete_endpoint, stage_git_file_endpoint, stage_lfs_file_endpoint
from datasets.browser_commits import BrowserCommitUnavailable, BrowserDraftConflict, BrowserDraftForbidden, BrowserDraftInvalid
from datasets.lfs_transfers import LfsIntegrityError, LfsObjectMissing, LfsTransferUnavailable
from datasets.models import BrowserCommitDraft, LfsObject
from datasets.object_storage import ObjectStoreError


class FakeRequest:
    """Provide browser-commit endpoint request attributes."""

    def __init__(self, *, content_type='application/octet-stream'):
        """Initialize an authenticated request with selected media type."""

        self.auth = SimpleNamespace(id=7)
        self.content_type = content_type


class BrowserCommitApiContractTests(SimpleTestCase):
    """Exercise browser commit authorization and domain-error response mapping."""

    dataset_id = uuid4()
    draft_id = uuid4()

    def setUp(self):
        """Create stable dataset, draft, and serialized response doubles."""

        self.request = FakeRequest()
        self.dataset = SimpleNamespace(id=self.dataset_id)
        self.draft = SimpleNamespace(id=self.draft_id, pk=self.draft_id, target_branch='main')
        self.serialized = {'id': str(self.draft_id), 'changes': []}

    def assert_status(self, response, expected_status, expected_code):
        """Assert one django-ninja status response."""

        self.assertEqual(response.status_code, expected_status)
        self.assertEqual(response.value['code'], expected_code)

    def endpoint_calls(self):
        """Return all endpoint invocations at the common authorization boundary."""

        return [
            lambda: create_draft_endpoint(self.request, self.dataset_id, DraftCreateInput(target_branch='main')),
            lambda: list_drafts_endpoint(self.request, self.dataset_id),
            lambda: get_draft_endpoint(self.request, self.dataset_id, self.draft_id),
            lambda: discard_draft_endpoint(self.request, self.dataset_id, self.draft_id),
            lambda: stage_git_file_endpoint(self.request, self.dataset_id, self.draft_id, 'data.csv', 4),
            lambda: stage_lfs_file_endpoint(self.request, self.dataset_id, self.draft_id, LfsStageInput(path='data.bin', oid='a' * 64, size=4)),
            lambda: complete_lfs_file_endpoint(self.request, self.dataset_id, self.draft_id, 'a' * 64),
            lambda: stage_delete_endpoint(self.request, self.dataset_id, self.draft_id, 'data.csv'),
            lambda: publish_draft_endpoint(self.request, self.dataset_id, self.draft_id, DraftCommitInput(message='Commit data')),
        ]

    def test_every_endpoint_maps_write_denial_and_hidden_dataset(self):
        """Apply one consistent current-role boundary before draft lookup."""

        for endpoint in self.endpoint_calls():
            with self.subTest(endpoint=endpoint), patch('datasets.browser_commit_api._writable_dataset', side_effect=PermissionDenied):
                self.assert_status(endpoint(), 403, 'permission_denied')
            with self.subTest(endpoint=endpoint), patch('datasets.browser_commit_api._writable_dataset', return_value=None):
                self.assert_status(endpoint(), 404, 'dataset_not_found')

    def test_draft_create_list_get_and_discard_contracts(self):
        """Create, resume, inspect, and discard creator-private drafts."""

        with patch('datasets.browser_commit_api._writable_dataset', return_value=self.dataset), patch('datasets.browser_commit_api.create_browser_draft', return_value=self.draft), patch('datasets.browser_commit_api._serialize_draft', return_value=self.serialized):
            created = create_draft_endpoint(self.request, self.dataset_id, DraftCreateInput(target_branch='main'))
        self.assertEqual(created.status_code, 201)

        manager = Mock()
        queryset = manager.filter.return_value.order_by.return_value
        filtered = MagicMock()
        filtered.__getitem__.return_value = [self.draft]
        queryset.filter.return_value = filtered
        with patch('datasets.browser_commit_api._writable_dataset', return_value=self.dataset), patch('datasets.browser_commit_api.BrowserCommitDraft.objects', manager), patch('datasets.browser_commit_api._serialize_draft', return_value=self.serialized):
            listed = list_drafts_endpoint(self.request, self.dataset_id, target_branch='main')
        self.assertEqual(listed, {'count': 1, 'items': [self.serialized]})
        manager.filter.return_value.order_by.return_value.filter.assert_called_once_with(target_branch='main')

        for found, expected in ((self.draft, self.serialized), (None, None)):
            with patch('datasets.browser_commit_api._writable_dataset', return_value=self.dataset), patch('datasets.browser_commit_api._creator_draft', return_value=found), patch('datasets.browser_commit_api._serialize_draft', return_value=self.serialized):
                response = get_draft_endpoint(self.request, self.dataset_id, self.draft_id)
            if expected is None:
                self.assert_status(response, 404, 'draft_not_found')
            else:
                self.assertEqual(response, expected)

        with patch('datasets.browser_commit_api._writable_dataset', return_value=self.dataset), patch('datasets.browser_commit_api._creator_draft', return_value=self.draft), patch('datasets.browser_commit_api.discard_browser_draft', return_value=self.draft), patch('datasets.browser_commit_api._serialize_draft', return_value=self.serialized):
            self.assertEqual(discard_draft_endpoint(self.request, self.dataset_id, self.draft_id), self.serialized)

        for found, error, status, code in ((None, None, 404, 'draft_not_found'), (self.draft, BrowserDraftInvalid('closed'), 422, 'draft_invalid')):
            with self.subTest(code=code), patch('datasets.browser_commit_api._writable_dataset', return_value=self.dataset), patch('datasets.browser_commit_api._creator_draft', return_value=found), patch('datasets.browser_commit_api.discard_browser_draft', side_effect=error):
                self.assert_status(discard_draft_endpoint(self.request, self.dataset_id, self.draft_id), status, code)

    def test_draft_create_maps_validation_and_repository_outage(self):
        """Keep branch validation distinct from transient Git failure."""

        for error, status, code in ((BrowserDraftInvalid('invalid'), 422, 'draft_invalid'), (BrowserCommitUnavailable('down'), 503, 'repository_unavailable')):
            with self.subTest(error=type(error).__name__), patch('datasets.browser_commit_api._writable_dataset', return_value=self.dataset), patch('datasets.browser_commit_api.create_browser_draft', side_effect=error):
                self.assert_status(create_draft_endpoint(self.request, self.dataset_id, DraftCreateInput(target_branch='main')), status, code)

    def test_git_file_staging_validates_transport_and_maps_domain_errors(self):
        """Bound ordinary Git content before invoking streaming storage."""

        with patch('datasets.browser_commit_api._writable_dataset', return_value=self.dataset):
            self.assert_status(stage_git_file_endpoint(self.request, self.dataset_id, self.draft_id, 'large.bin', 10 * 1024 * 1024 + 1), 413, 'git_blob_too_large')
            self.request.content_type = 'text/plain'
            self.assert_status(stage_git_file_endpoint(self.request, self.dataset_id, self.draft_id, 'data.txt', 4), 422, 'content_type_invalid')

        self.request.content_type = 'application/octet-stream'
        with patch('datasets.browser_commit_api._writable_dataset', return_value=self.dataset), patch('datasets.browser_commit_api._creator_draft', return_value=None):
            self.assert_status(stage_git_file_endpoint(self.request, self.dataset_id, self.draft_id, 'data.txt', 4), 404, 'draft_not_found')

        manager = Mock()
        manager.get.return_value = self.draft
        with patch('datasets.browser_commit_api._writable_dataset', return_value=self.dataset), patch('datasets.browser_commit_api._creator_draft', return_value=self.draft), patch('datasets.browser_commit_api.stage_git_blob') as stage, patch('datasets.browser_commit_api.BrowserCommitDraft.objects', manager), patch('datasets.browser_commit_api._serialize_draft', return_value=self.serialized):
            self.assertEqual(stage_git_file_endpoint(self.request, self.dataset_id, self.draft_id, 'data.txt', 4), self.serialized)
        stage.assert_called_once()

        for error, status, code in ((BrowserDraftInvalid('invalid'), 422, 'draft_invalid'), (BrowserCommitUnavailable('down'), 503, 'repository_unavailable')):
            with self.subTest(error=type(error).__name__), patch('datasets.browser_commit_api._writable_dataset', return_value=self.dataset), patch('datasets.browser_commit_api._creator_draft', return_value=self.draft), patch('datasets.browser_commit_api.stage_git_blob', side_effect=error):
                self.assert_status(stage_git_file_endpoint(self.request, self.dataset_id, self.draft_id, 'data.txt', 4), status, code)

    @override_settings(NIYAN_LFS_MULTIPART_THRESHOLD_BYTES=10)
    def test_lfs_staging_selects_existing_basic_and_multipart_transfers(self):
        """Choose transfer strategy from verified state and configured size threshold."""

        manager = Mock()
        manager.get.return_value = self.draft
        cases = [
            (LfsObject.State.AVAILABLE, 4, 'existing'),
            (LfsObject.State.PENDING, 4, 'basic'),
            (LfsObject.State.PENDING, 10, 'multipart'),
        ]
        for state, size, transfer in cases:
            lfs_object = SimpleNamespace(state=state, oid='a' * 64)
            change = SimpleNamespace(operation='upsert', storage='lfs', size=size, git_blob_oid='', lfs_object_id=1, lfs_object=lfs_object, path='data.bin')
            upload = SimpleNamespace(method='PUT', url='https://objects.example/upload', headers={}, expires_in=300)
            session = SimpleNamespace(id=uuid4(), part_size=5, expected_part_count=2)
            payload = LfsStageInput(path='data.bin', oid='a' * 64, size=size)
            with self.subTest(transfer=transfer), patch('datasets.browser_commit_api._writable_dataset', return_value=self.dataset), patch('datasets.browser_commit_api._creator_draft', return_value=self.draft), patch('datasets.browser_commit_api.stage_lfs_object', return_value=change), patch('datasets.browser_commit_api.issue_upload_action', return_value=upload), patch('datasets.browser_commit_api.initiate_multipart_upload', return_value=session):
                response = stage_lfs_file_endpoint(self.request, self.dataset_id, self.draft_id, payload)
            self.assertEqual(response['transfer'], transfer)

    def test_lfs_staging_maps_missing_draft_and_storage_failures(self):
        """Keep object identity, availability, and provider failures distinct."""

        payload = LfsStageInput(path='data.bin', oid='a' * 64, size=4)
        with patch('datasets.browser_commit_api._writable_dataset', return_value=self.dataset), patch('datasets.browser_commit_api._creator_draft', return_value=None):
            self.assert_status(stage_lfs_file_endpoint(self.request, self.dataset_id, self.draft_id, payload), 404, 'draft_not_found')

        mappings = [
            (BrowserDraftInvalid('invalid'), 422, 'draft_invalid'),
            (BrowserCommitUnavailable('down'), 503, 'repository_unavailable'),
            (LfsIntegrityError('invalid'), 422, 'lfs_object_invalid'),
            (LfsTransferUnavailable('down'), 503, 'object_storage_unavailable'),
            (ObjectStoreError('provider'), 503, 'object_storage_unavailable'),
        ]
        for error, status, code in mappings:
            with self.subTest(error=type(error).__name__), patch('datasets.browser_commit_api._writable_dataset', return_value=self.dataset), patch('datasets.browser_commit_api._creator_draft', return_value=self.draft), patch('datasets.browser_commit_api.stage_lfs_object', side_effect=error):
                self.assert_status(stage_lfs_file_endpoint(self.request, self.dataset_id, self.draft_id, payload), status, code)

    def test_lfs_completion_refreshes_draft_and_maps_failures(self):
        """Verify an uploaded object before showing the staged change as ready."""

        change = SimpleNamespace(lfs_object=object())
        changes = Mock()
        changes.select_related.return_value.filter.return_value.first.return_value = change
        self.draft.changes = changes
        manager = Mock()
        manager.get.return_value = self.draft
        with patch('datasets.browser_commit_api._writable_dataset', return_value=self.dataset), patch('datasets.browser_commit_api._creator_draft', return_value=self.draft), patch('datasets.browser_commit_api.finalize_lfs_upload') as finalize, patch('datasets.browser_commit_api.BrowserCommitDraft.objects', manager), patch('datasets.browser_commit_api._serialize_draft', return_value=self.serialized):
            self.assertEqual(complete_lfs_file_endpoint(self.request, self.dataset_id, self.draft_id, 'a' * 64), self.serialized)
        finalize.assert_called_once_with(lfs_object=change.lfs_object)

        changes.select_related.return_value.filter.return_value.first.return_value = None
        with patch('datasets.browser_commit_api._writable_dataset', return_value=self.dataset), patch('datasets.browser_commit_api._creator_draft', return_value=self.draft):
            self.assert_status(complete_lfs_file_endpoint(self.request, self.dataset_id, self.draft_id, 'a' * 64), 404, 'draft_change_not_found')
        changes.select_related.return_value.filter.return_value.first.return_value = change

        mappings = [
            (LfsObjectMissing('missing'), 404, 'lfs_object_not_found'),
            (LfsIntegrityError('invalid'), 422, 'lfs_object_invalid'),
            (LfsTransferUnavailable('down'), 503, 'object_storage_unavailable'),
            (ObjectStoreError('provider'), 503, 'object_storage_unavailable'),
        ]
        for error, status, code in mappings:
            with self.subTest(error=type(error).__name__), patch('datasets.browser_commit_api._writable_dataset', return_value=self.dataset), patch('datasets.browser_commit_api._creator_draft', return_value=self.draft), patch('datasets.browser_commit_api.finalize_lfs_upload', side_effect=error):
                self.assert_status(complete_lfs_file_endpoint(self.request, self.dataset_id, self.draft_id, 'a' * 64), status, code)

    def test_delete_and_publish_map_success_and_rejections(self):
        """Apply staged deletion and audit every rejected publication."""

        manager = Mock()
        manager.get.return_value = self.draft
        for target, endpoint in (
            ('stage_delete', lambda: stage_delete_endpoint(self.request, self.dataset_id, self.draft_id, 'data.csv')),
            ('publish_browser_draft', lambda: publish_draft_endpoint(self.request, self.dataset_id, self.draft_id, DraftCommitInput(message='Commit data'))),
        ):
            with self.subTest(target=target), patch('datasets.browser_commit_api._writable_dataset', return_value=self.dataset), patch('datasets.browser_commit_api._creator_draft', return_value=self.draft), patch(f'datasets.browser_commit_api.{target}', return_value=self.draft), patch('datasets.browser_commit_api.BrowserCommitDraft.objects', manager), patch('datasets.browser_commit_api._serialize_draft', return_value=self.serialized):
                self.assertEqual(endpoint(), self.serialized)

        for endpoint in (lambda: stage_delete_endpoint(self.request, self.dataset_id, self.draft_id, 'data.csv'), lambda: publish_draft_endpoint(self.request, self.dataset_id, self.draft_id, DraftCommitInput(message='Commit data'))):
            with self.subTest(endpoint=endpoint), patch('datasets.browser_commit_api._writable_dataset', return_value=self.dataset), patch('datasets.browser_commit_api._creator_draft', return_value=None):
                self.assert_status(endpoint(), 404, 'draft_not_found')

        for error, status, code in ((BrowserDraftConflict('moved'), 409, 'draft_conflict'), (BrowserDraftForbidden('denied'), 403, 'permission_denied'), (BrowserDraftInvalid('invalid'), 422, 'draft_invalid'), (BrowserCommitUnavailable('down'), 503, 'repository_unavailable')):
            with self.subTest(error=type(error).__name__), patch('datasets.browser_commit_api._writable_dataset', return_value=self.dataset), patch('datasets.browser_commit_api._creator_draft', return_value=self.draft), patch('datasets.browser_commit_api.publish_browser_draft', side_effect=error), patch('datasets.browser_commit_api.record_audit_event') as audit:
                self.assert_status(publish_draft_endpoint(self.request, self.dataset_id, self.draft_id, DraftCommitInput(message='Commit data')), status, code)
            audit.assert_called_once()

        for error, status, code in ((BrowserDraftInvalid('invalid'), 422, 'draft_invalid'), (BrowserCommitUnavailable('down'), 503, 'repository_unavailable')):
            with self.subTest(error=type(error).__name__), patch('datasets.browser_commit_api._writable_dataset', return_value=self.dataset), patch('datasets.browser_commit_api._creator_draft', return_value=self.draft), patch('datasets.browser_commit_api.stage_delete', side_effect=error):
                self.assert_status(stage_delete_endpoint(self.request, self.dataset_id, self.draft_id, 'data.csv'), status, code)


if __name__ == '__main__':
    unittest.main()
