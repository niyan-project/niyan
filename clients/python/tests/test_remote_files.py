import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from niyan.cli import build_parser, main
from niyan.config import AppPaths
from niyan.remote_files import download_remote_paths, show_remote_tree, stream_remote_file


COMMIT = 'a' * 40


class FakeRemoteFileSystem:
    """Record how checkout-free commands reuse the filesystem contract."""

    instances = []

    def __init__(self, **options):
        """Retain construction options and transfer calls."""

        self.options = options
        self.calls = []
        type(self).instances.append(self)

    def info(self, path):
        """Return a pinned root, directory, or file representation."""

        self.calls.append(('info', path))
        if path in ('', 'batch'):
            return {'type': 'directory', 'resolved_commit': COMMIT, 'repository_path': path}
        return {'type': 'file', 'resolved_commit': COMMIT, 'repository_path': path, 'size': 7}

    def ls(self, path, detail=True):
        """Return one ordinary blob, LFS object, and directory."""

        self.calls.append(('ls', path, detail))
        return [
            {'type': 'file', 'size': 7, 'repository_path': 'notes.txt', 'is_lfs': False},
            {'type': 'file', 'size': 12, 'repository_path': 'large.bin', 'is_lfs': True},
            {'type': 'directory', 'size': 0, 'repository_path': 'batch', 'is_lfs': False},
        ]

    def open(self, path, mode):
        """Return a bounded binary stream for cat."""

        self.calls.append(('open', path, mode))
        return io.BytesIO(b'exact file content')

    def get_file(self, source, destination):
        """Record and materialize one file destination."""

        self.calls.append(('get_file', source, Path(destination)))
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        Path(destination).write_bytes(b'file')

    def get(self, source, destination, recursive=False):
        """Record and materialize one recursive destination."""

        self.calls.append(('get', source, Path(destination), recursive))
        Path(destination).mkdir(parents=True, exist_ok=True)


class RemoteFileCommandTests(unittest.TestCase):
    """Verify CLI remote reads pin revisions and preserve output boundaries."""

    def setUp(self):
        """Reset fake filesystem observations."""

        FakeRemoteFileSystem.instances = []
        self.paths = object()
        self.stores = object()

    def test_tree_prints_direct_children_with_storage_type(self):
        """Render paginated filesystem metadata without a checkout."""

        output = io.StringIO()
        items = show_remote_tree(host='https://niyan.example', dataset_path='lab/images', repository_path='', revision='main', paths=self.paths, stores=self.stores, cwd=Path('/tmp'), stdout=output, filesystem_factory=FakeRemoteFileSystem)

        self.assertEqual(len(items), 3)
        self.assertEqual(output.getvalue(), 'TYPE\tSIZE\tPATH\nblob\t7\tnotes.txt\nlfs\t12\tlarge.bin\ntree\t-\tbatch\n')

    def test_cat_reserves_stdout_for_exact_binary_content(self):
        """Write no headings, progress, or diagnostics around file bytes."""

        output = io.BytesIO()
        stream_remote_file(host='https://niyan.example', dataset_path='lab/images', repository_path='notes.txt', revision='main', paths=self.paths, stores=self.stores, cwd=Path('/tmp'), stdout=output, filesystem_factory=FakeRemoteFileSystem)

        self.assertEqual(output.getvalue(), b'exact file content')

    def test_download_pins_once_then_preserves_repository_paths(self):
        """Use one exact commit for every file and directory in the operation."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = download_remote_paths(host='https://niyan.example', dataset_path='lab/images', repository_paths=['notes.txt', 'batch'], revision='main', output_directory='downloads', paths=self.paths, stores=self.stores, cwd=root, filesystem_factory=FakeRemoteFileSystem)

            self.assertEqual(result['resolved_commit'], COMMIT)
            self.assertEqual(result['output_directory'], str(root / 'downloads'))
            self.assertEqual(len(FakeRemoteFileSystem.instances), 2)
            self.assertEqual(FakeRemoteFileSystem.instances[0].options['revision'], 'main')
            pinned = FakeRemoteFileSystem.instances[1]
            self.assertEqual(pinned.options['revision'], COMMIT)
            self.assertIn(('get_file', 'notes.txt', root / 'downloads' / 'notes.txt'), pinned.calls)
            self.assertIn(('get', 'batch', root / 'downloads' / 'batch', True), pinned.calls)

    def test_parser_and_dispatch_expose_tree_cat_and_download(self):
        """Connect the accepted command grammar to the remote-file functions."""

        tree = build_parser().parse_args(['dataset', 'tree', 'lab/images', 'raw', '--ref', 'v1'])
        cat = build_parser().parse_args(['dataset', 'cat', 'lab/images', 'README.md'])
        download = build_parser().parse_args(['dataset', 'download', 'lab/images', 'raw', 'labels.csv', '--output', 'data'])
        self.assertEqual(tree.revision, 'v1')
        self.assertEqual(cat.repository_path, 'README.md')
        self.assertEqual(download.repository_paths, ['raw', 'labels.csv'])

        paths = AppPaths(config_home=Path('/tmp/config'), data_home=Path('/tmp/data'))
        stores = object()
        with patch('niyan.cli.AppPaths.from_environment', return_value=paths), patch('niyan.cli.CredentialStores', return_value=stores), patch('niyan.cli.resolve_host', return_value='https://niyan.example'), patch('niyan.cli.show_remote_tree') as tree_call:
            status = main(['dataset', 'tree', 'lab/images', 'raw', '--ref', 'main'])
        self.assertEqual(status, 0)
        self.assertEqual(tree_call.call_args.kwargs['repository_path'], 'raw')
        self.assertEqual(tree_call.call_args.kwargs['revision'], 'main')


if __name__ == '__main__':
    unittest.main()
