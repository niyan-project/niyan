import unittest

import fsspec

from niyan.config import Configuration, CredentialBinding
from niyan.errors import ApiError
from niyan.filesystem import NiyanFileSystem, NiyanPermissionError


class FakeFileSystemApi:
    """Serve deterministic filesystem metadata without network access."""

    calls = []

    def __init__(self, host, *, token=None, timeout=30):
        self.host = host
        self.token = token
        self.timeout = timeout

    def capabilities(self):
        self.calls.append(('capabilities', self.host, self.token))
        return 200, {'api_versions': ['v1'], 'filesystem': {'protocol_version': 1, 'features': ['dataset-path-resolution', 'exact-revision-resolution', 'repository-metadata']}}

    def resolve_dataset(self, locator_path):
        self.calls.append(('resolve_dataset', locator_path))
        if locator_path.startswith('lab/images'):
            remainder = locator_path.removeprefix('lab/images').strip('/')
            return 200, {'id': 'dataset-id', 'path': 'lab/images', 'repository_path': remainder}
        raise ApiError('The requested dataset does not exist.', status=404, code='dataset_not_found')

    def resolve_revision(self, dataset_id, *, revision=None):
        self.calls.append(('resolve_revision', dataset_id, revision))
        return 200, {'dataset_id': dataset_id, 'requested_revision': revision or 'main', 'resolved_commit': 'a' * 40}

    def list_repository_tree(self, dataset_id, *, revision, path='', limit=100, offset=0):
        self.calls.append(('list_tree', dataset_id, revision, path, limit, offset))
        if path == 'nested':
            return 200, {'items': [{'name': 'sample.csv', 'path': 'nested/sample.csv', 'object_type': 'blob', 'object_id': 'c' * 40, 'size': 42, 'is_lfs': False, 'lfs_object_id': None}], 'next_offset': None}
        if path:
            raise ApiError('The requested repository path does not exist.', status=404, code='path_not_found')
        return 200, {
            'items': [
                {'name': 'nested', 'path': 'nested', 'object_type': 'tree', 'object_id': 'b' * 40, 'size': None, 'is_lfs': False, 'lfs_object_id': None},
                {'name': 'large.bin', 'path': 'large.bin', 'object_type': 'blob', 'object_id': 'd' * 40, 'size': 5_000_000_000, 'is_lfs': True, 'lfs_object_id': 'e' * 64},
            ],
            'next_offset': None,
        }

    def get_repository_blob(self, dataset_id, *, revision, path):
        self.calls.append(('get_blob', dataset_id, revision, path))
        if path == 'large.bin':
            return 200, {'object_id': 'd' * 40, 'size': 130, 'is_lfs': True, 'lfs_object_id': 'e' * 64, 'lfs_size': 5_000_000_000}
        if path == 'nested/sample.csv':
            return 200, {'object_id': 'c' * 40, 'size': 42, 'is_lfs': False, 'lfs_object_id': None, 'lfs_size': None}
        raise ApiError('The requested repository path does not exist.', status=404, code='path_not_found')


class FileSystemMetadataTests(unittest.TestCase):
    """Verify the registered read-only fsspec metadata surface."""

    def setUp(self):
        """Reset fake transport observations before each test."""

        FakeFileSystemApi.calls = []
        self.fs = NiyanFileSystem(token='niyan_test-token', api_factory=FakeFileSystemApi)

    def test_canonical_url_lists_logical_lfs_metadata_at_one_commit(self):
        """Resolve a locator and revision once before listing direct children."""

        items = self.fs.ls('niyan://data.example.test/lab/images?revision=main', detail=True)

        self.assertEqual([item['repository_path'] for item in items], ['nested', 'large.bin'])
        self.assertEqual(items[1]['size'], 5_000_000_000)
        self.assertTrue(items[1]['is_lfs'])
        self.assertEqual(items[1]['resolved_commit'], 'a' * 40)
        self.assertIn(('resolve_revision', 'dataset-id', 'main'), FakeFileSystemApi.calls)
        self.assertIn(('list_tree', 'dataset-id', 'a' * 40, '', 100, 0), FakeFileSystemApi.calls)

    def test_info_exists_isfile_and_isdir_use_standard_fsspec_shapes(self):
        """Expose ordinary metadata predicates without requiring a checkout."""

        file_info = self.fs.info('niyan://data.example.test/lab/images/large.bin?revision=v1')

        self.assertEqual(file_info['type'], 'file')
        self.assertEqual(file_info['size'], 5_000_000_000)
        self.assertTrue(self.fs.isfile('niyan://data.example.test/lab/images/nested/sample.csv?revision=v1'))
        self.assertTrue(self.fs.isdir('niyan://data.example.test/lab/images/nested?revision=v1'))
        self.assertFalse(self.fs.exists('niyan://data.example.test/lab/images/missing?revision=v1'))

    def test_direct_construction_treats_paths_as_repository_relative(self):
        """Support applications that keep connection options outside URLs."""

        fs = NiyanFileSystem(host='data.example.test', dataset='lab/images', revision='v1', token='niyan_test-token', api_factory=FakeFileSystemApi)

        self.assertEqual(fs.info('nested/sample.csv')['repository_path'], 'nested/sample.csv')

    def test_url_options_must_agree_and_unknown_queries_are_rejected(self):
        """Keep host, revision, and cache identity unambiguous."""

        fs = NiyanFileSystem(host='other.example.test', token='niyan_test-token', api_factory=FakeFileSystemApi)
        with self.assertRaisesRegex(ValueError, 'authority'):
            fs.info('niyan://data.example.test/lab/images')
        with self.assertRaisesRegex(ValueError, 'only one revision'):
            self.fs.info('niyan://data.example.test/lab/images?version=main')

    def test_permission_errors_are_not_collapsed_into_false_existence(self):
        """Preserve authorization failures instead of pretending resources are absent."""

        class DeniedApi(FakeFileSystemApi):
            def resolve_dataset(self, locator_path):
                raise ApiError('Denied', status=403, code='permission_denied')

        fs = NiyanFileSystem(token='niyan_test-token', api_factory=DeniedApi)

        with self.assertRaises(NiyanPermissionError):
            fs.exists('niyan://data.example.test/lab/images')

    def test_fsspec_discovers_the_installed_protocol_entry_point(self):
        """Construct Niyān through fsspec without importing CLI modules."""

        filesystem = fsspec.filesystem('niyan', host='data.example.test', dataset='lab/images', token='niyan_test-token', api_factory=FakeFileSystemApi)

        self.assertIsInstance(filesystem, NiyanFileSystem)

    def test_dataset_binding_is_selected_from_a_full_file_locator(self):
        """Prefer an exact dataset token before a broader host credential."""

        binding = CredentialBinding(token_id='dataset-token', username='researcher', storage='file', dataset_id='00000000-0000-0000-0000-000000000001', dataset_path='lab/images', resource_boundary='dataset')
        configuration = Configuration()
        configuration.set_binding(host='https://data.example.test', binding=binding)

        self.assertEqual(configuration.select_binding(host='https://data.example.test', dataset_path='lab/images/nested/sample.csv'), binding)


if __name__ == '__main__':
    unittest.main()
