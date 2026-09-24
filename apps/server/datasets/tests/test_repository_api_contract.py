from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase

from datasets.lfs_transfers import LfsTransferUnavailable
from datasets.object_storage import ObjectStoreError
from datasets.repository_api import _parse_byte_range, authorize_repository_download_endpoint, display_repository_content_endpoint, download_repository_blob_endpoint, get_repository_blob_endpoint, get_repository_commit_endpoint, get_repository_readme_endpoint, get_repository_text_endpoint, list_repository_commits_endpoint, list_repository_refs_endpoint, list_repository_tree_endpoint, resolve_repository_revision_endpoint, serialize_blob
from datasets.repository_browser import InvalidRepositoryInput, LfsContentUnavailable, RepositoryBrowseError, RepositoryPathNotFound, RevisionNotFound, TextPreviewUnavailable


class FakeRequest:
    """Provide the request attributes used by repository endpoints."""

    def __init__(self, *, range_header=None):
        """Initialize authentication, headers, and URL construction."""

        self.auth = object()
        self.headers = {} if range_header is None else {'Range': range_header}

    def build_absolute_uri(self, path):
        """Return a stable installation URL for authorized Git downloads."""

        return f'https://data.example.test{path}'


class FakeBrowser:
    """Expose deterministic successful repository browsing results."""

    commit = 'a' * 40

    def __init__(self, *, lfs=False):
        """Select ordinary Git or LFS metadata."""

        self.metadata = SimpleNamespace(path='data/file.bin', object_id='b' * 40, size=7, lfs_object_id='c' * 64 if lfs else None, lfs_size=11 if lfs else None)

    def list_refs(self, **options):
        """Return one branch and no next page."""

        return [{'name': 'main'}], None

    def list_commits(self, **options):
        """Return one commit and no next page."""

        return self.commit, [{'object_id': self.commit}], None

    def get_commit(self, **options):
        """Return one complete exact commit."""

        return self.commit, {'object_id': self.commit, 'parent_ids': [], 'author_name': 'Researcher', 'author_email': '', 'authored_at': '2030-01-01T00:00:00Z', 'subject': 'Commit subject', 'body': ''}

    def resolve_revision(self, revision):
        """Resolve every accepted revision to one commit."""

        return self.commit

    def list_tree(self, **options):
        """Return one tree entry and no next page."""

        return self.commit, [{'path': 'data'}], None

    def get_blob_metadata(self, **options):
        """Return selected file metadata."""

        return self.commit, self.metadata

    def read_text(self, **options):
        """Return one plain-text Git blob."""

        if self.metadata.lfs_object_id is not None:
            raise TextPreviewUnavailable()
        return self.commit, self.metadata, 'content'

    def open_blob_range(self, **options):
        """Stream the requested byte interval."""

        start = options['start']
        end = options['end']
        return self.commit, self.metadata, [b'content'[start:end]]

    def read_readme(self, **options):
        """Return README metadata and decoded content."""

        metadata = SimpleNamespace(path='README.md', object_id='d' * 40, size=8, lfs_object_id=None, lfs_size=None)
        return self.commit, metadata, '# Data\n'


class RepositoryApiContractTests(SimpleTestCase):
    """Exercise public repository response mapping without duplicating Git tests."""

    dataset_id = uuid4()

    def assert_status(self, response, expected_status, expected_code):
        """Assert one django-ninja status wrapper."""

        self.assertEqual(response.status_code, expected_status)
        self.assertEqual(response.value['code'], expected_code)

    def test_list_and_resolution_endpoints_serialize_success(self):
        """Return stable pages and exact revision identities."""

        browser = FakeBrowser()
        request = FakeRequest()
        with patch('datasets.repository_api.get_browser', return_value=browser):
            refs = list_repository_refs_endpoint(request, self.dataset_id, kind='tags', limit=25, offset=5)
            commits = list_repository_commits_endpoint(request, self.dataset_id, revision='main', limit=20, offset=4)
            resolution = resolve_repository_revision_endpoint(request, self.dataset_id, revision='main')
            tree = list_repository_tree_endpoint(request, self.dataset_id, revision='main', path='/data/', limit=30, offset=3)
            blob = get_repository_blob_endpoint(request, self.dataset_id, path='data/file.bin', revision='main')
            commit = get_repository_commit_endpoint(request, self.dataset_id, commit_id=FakeBrowser.commit)
            text = get_repository_text_endpoint(request, self.dataset_id, path='data/file.bin', revision='main')

        self.assertEqual(refs, {'kind': 'tags', 'limit': 25, 'offset': 5, 'next_offset': None, 'items': [{'name': 'main'}]})
        self.assertEqual(commits['resolved_commit'], FakeBrowser.commit)
        self.assertEqual(resolution['dataset_id'], self.dataset_id)
        self.assertEqual(tree['path'], 'data')
        self.assertEqual(blob, serialize_blob(FakeBrowser.commit, browser.metadata))
        self.assertEqual(commit['object_id'], FakeBrowser.commit)
        self.assertIsNone(commit['author_user'])
        self.assertEqual(text['content'], 'content')

    def test_readme_lfs_resource_redirects_to_direct_object_storage(self):
        """Keep relative README images out of Django's bulk-byte path."""

        browser = FakeBrowser(lfs=True)
        lfs_object = SimpleNamespace()
        objects = Mock()
        objects.select_related.return_value.filter.return_value.first.return_value = lfs_object
        with patch('datasets.repository_api.get_browser', return_value=browser), patch('datasets.repository_api.LfsObject.objects', objects), patch('datasets.repository_api.issue_download_action', return_value=SimpleNamespace(url='https://objects.example.test/signed')):
            response = display_repository_content_endpoint(FakeRequest(), self.dataset_id, path='images/scan.png', revision='main')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://objects.example.test/signed')

    def test_list_endpoints_hide_missing_datasets_and_map_domain_errors(self):
        """Translate repository failures to stable non-sensitive responses."""

        request = FakeRequest()
        endpoints = [
            lambda: list_repository_refs_endpoint(request, self.dataset_id),
            lambda: list_repository_commits_endpoint(request, self.dataset_id),
            lambda: resolve_repository_revision_endpoint(request, self.dataset_id),
            lambda: list_repository_tree_endpoint(request, self.dataset_id),
            lambda: get_repository_blob_endpoint(request, self.dataset_id, path='data/file.bin'),
        ]
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                with patch('datasets.repository_api.get_browser', return_value=None):
                    self.assert_status(endpoint(), 404, 'dataset_not_found')

        mappings = [
            (RevisionNotFound(), 404, 'revision_not_found'),
            (RepositoryPathNotFound(), 404, 'path_not_found'),
            (InvalidRepositoryInput(), 422, 'validation_error'),
            (RepositoryBrowseError(), 503, 'repository_unavailable'),
        ]
        for error, status, code in mappings:
            for endpoint in endpoints[1:]:
                with self.subTest(error=type(error).__name__, endpoint=endpoint):
                    browser = FakeBrowser()
                    browser.list_commits = Mock(side_effect=error)
                    browser.resolve_revision = Mock(side_effect=error)
                    browser.list_tree = Mock(side_effect=error)
                    browser.get_blob_metadata = Mock(side_effect=error)
                    with patch('datasets.repository_api.get_browser', return_value=browser):
                        response = endpoint()
                    if isinstance(error, RepositoryPathNotFound) and endpoint in endpoints[1:3]:
                        continue
                    self.assert_status(response, status, code)

        for error, status, code in ((InvalidRepositoryInput(), 422, 'validation_error'), (RepositoryBrowseError(), 503, 'repository_unavailable')):
            browser = FakeBrowser()
            browser.list_refs = Mock(side_effect=error)
            with patch('datasets.repository_api.get_browser', return_value=browser):
                self.assert_status(list_repository_refs_endpoint(request, self.dataset_id), status, code)

    def test_commit_and_text_endpoints_map_specific_unavailable_content(self):
        """Keep exact-commit and text-preview failures stable."""

        request = FakeRequest()
        browser = FakeBrowser()
        browser.get_commit = Mock(side_effect=RevisionNotFound())
        with patch('datasets.repository_api.get_browser', return_value=browser):
            self.assert_status(get_repository_commit_endpoint(request, self.dataset_id, commit_id='missing'), 404, 'commit_not_found')

        with patch('datasets.repository_api.get_browser', return_value=FakeBrowser(lfs=True)):
            self.assert_status(get_repository_text_endpoint(request, self.dataset_id, path='data/file.bin'), 409, 'text_preview_unavailable')

    def test_raw_blob_download_supports_full_partial_and_unsatisfied_ranges(self):
        """Stream Git bytes with correct range and immutable identity headers."""

        browser = FakeBrowser()
        with patch('datasets.repository_api.get_browser', return_value=browser):
            full = download_repository_blob_endpoint(FakeRequest(), self.dataset_id, path='data/file.bin')
            partial = download_repository_blob_endpoint(FakeRequest(range_header='bytes=1-3'), self.dataset_id, path='data/file.bin')
            invalid = download_repository_blob_endpoint(FakeRequest(range_header='bytes=100-200'), self.dataset_id, path='data/file.bin')

        self.assertEqual(full.status_code, 200)
        self.assertEqual(full['Content-Length'], '7')
        self.assertEqual(full['X-Niyan-Resolved-Commit'], FakeBrowser.commit)
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial['Content-Range'], 'bytes 1-3/7')
        self.assertEqual(b''.join(partial.streaming_content), b'ont')
        self.assertEqual(invalid.status_code, 416)
        self.assertEqual(invalid['Content-Range'], 'bytes */7')

    def test_raw_blob_download_maps_content_and_repository_failures(self):
        """Return stable statuses for LFS pointers and repository errors."""

        request = FakeRequest()
        with patch('datasets.repository_api.get_browser', return_value=FakeBrowser(lfs=True)):
            self.assert_status(download_repository_blob_endpoint(request, self.dataset_id, path='data/file.bin'), 409, 'lfs_content_unavailable')

        mappings = [
            (RevisionNotFound(), 404, 'revision_not_found'),
            (RepositoryPathNotFound(), 404, 'path_not_found'),
            (InvalidRepositoryInput(), 422, 'validation_error'),
            (RepositoryBrowseError(), 503, 'repository_unavailable'),
        ]
        for error, status, code in mappings:
            browser = FakeBrowser()
            browser.get_blob_metadata = Mock(side_effect=error)
            with self.subTest(error=type(error).__name__), patch('datasets.repository_api.get_browser', return_value=browser):
                self.assert_status(download_repository_blob_endpoint(request, self.dataset_id, path='data/file.bin'), status, code)

    def test_download_authorization_selects_git_or_lfs_without_proxying(self):
        """Issue a first-party Git URL or short-lived object-storage action."""

        request = FakeRequest()
        with patch('datasets.repository_api.get_browser', return_value=FakeBrowser()):
            git_action = authorize_repository_download_endpoint(request, self.dataset_id, path='data/file.bin')
        self.assertEqual(git_action['storage'], 'git')
        self.assertIn('/repository/blob/raw?', git_action['url'])

        lfs_object = SimpleNamespace()
        manager = Mock()
        manager.select_related.return_value.filter.return_value.first.return_value = lfs_object
        transfer = SimpleNamespace(method='GET', url='https://objects.example/download', headers={'Range': 'bytes=0-'}, expires_in=300)
        with patch('datasets.repository_api.get_browser', return_value=FakeBrowser(lfs=True)), patch('datasets.repository_api.LfsObject.objects', manager), patch('datasets.repository_api.issue_download_action', return_value=transfer):
            lfs_action = authorize_repository_download_endpoint(request, self.dataset_id, path='data/file.bin')
        self.assertEqual(lfs_action['storage'], 'lfs')
        self.assertEqual(lfs_action['url'], 'https://objects.example/download')

        manager.select_related.return_value.filter.return_value.first.return_value = None
        with patch('datasets.repository_api.get_browser', return_value=FakeBrowser(lfs=True)), patch('datasets.repository_api.LfsObject.objects', manager):
            self.assert_status(authorize_repository_download_endpoint(request, self.dataset_id, path='data/file.bin'), 409, 'lfs_content_unavailable')

    def test_download_authorization_and_readme_map_failures(self):
        """Sanitize storage and repository errors across non-streaming reads."""

        request = FakeRequest()
        browser = FakeBrowser()
        manager = Mock()
        manager.select_related.return_value.filter.return_value.first.return_value = SimpleNamespace()
        for error in (LfsTransferUnavailable(), ObjectStoreError('provider detail')):
            with self.subTest(error=type(error).__name__), patch('datasets.repository_api.get_browser', return_value=FakeBrowser(lfs=True)), patch('datasets.repository_api.LfsObject.objects', manager), patch('datasets.repository_api.issue_download_action', side_effect=error):
                self.assert_status(authorize_repository_download_endpoint(request, self.dataset_id, path='data/file.bin'), 503, 'download_unavailable')

        with patch('datasets.repository_api.get_browser', return_value=browser):
            readme = get_repository_readme_endpoint(request, self.dataset_id)
        self.assertEqual(readme['content'], '# Data\n')

        mappings = [
            (LfsContentUnavailable(), 409, 'readme_unavailable'),
            (RevisionNotFound(), 404, 'revision_not_found'),
            (RepositoryPathNotFound(), 404, 'readme_not_found'),
            (InvalidRepositoryInput(), 422, 'validation_error'),
            (RepositoryBrowseError(), 503, 'repository_unavailable'),
        ]
        for error, status, code in mappings:
            browser = FakeBrowser()
            browser.read_readme = Mock(side_effect=error)
            with self.subTest(error=type(error).__name__), patch('datasets.repository_api.get_browser', return_value=browser):
                self.assert_status(get_repository_readme_endpoint(request, self.dataset_id), status, code)

    def test_byte_range_parser_accepts_standard_forms_and_rejects_invalid_ones(self):
        """Cover bounded, open-ended, and suffix byte ranges."""

        self.assertIsNone(_parse_byte_range(None, size=10))
        self.assertEqual(_parse_byte_range('bytes=2-5', size=10), (2, 6))
        self.assertEqual(_parse_byte_range('bytes=2-', size=10), (2, 10))
        self.assertEqual(_parse_byte_range('bytes=-4', size=10), (6, 10))
        self.assertEqual(_parse_byte_range('bytes=-20', size=10), (0, 10))
        for value, size in (('items=0-1', 10), ('bytes=0-1,3-4', 10), ('bytes=-', 10), ('bytes=-0', 10), ('bytes=-1', 0), ('bytes=a-b', 10), ('bytes=-a', 10), ('bytes=-1-2', 10), ('bytes=-1', 0), ('bytes=10-', 10), ('bytes=5-2', 10), ('bytes=-2-3', 10)):
            with self.subTest(value=value, size=size):
                with self.assertRaises(ValueError):
                    _parse_byte_range(value, size=size)


if __name__ == '__main__':
    unittest.main()
