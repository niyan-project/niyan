import hashlib
import tempfile
import unittest
from urllib.error import URLError
from pathlib import Path

from niyan.errors import ApiError
from niyan.filesystem import NiyanFileSystem, NiyanIntegrityError


COMMIT = 'a' * 40
LFS_CONTENT = b'complete immutable lfs content'
LFS_OID = hashlib.sha256(LFS_CONTENT).hexdigest()
FILES = {
    'batch/first.txt': b'first file',
    'batch/nested/second.txt': b'second file',
}


class DownloadApi:
    """Serve pinned trees and authorized actions for download tests."""

    instances = []

    def __init__(self, host, *, token=None, timeout=30):
        self.host = host
        self.token = token
        self.timeout = timeout
        self.revision_resolutions = []
        self.tree_requests = []
        self.authorizations = []
        type(self).instances.append(self)

    def capabilities(self):
        return 200, {'api_versions': ['v1'], 'filesystem': {'protocol_version': 1, 'features': ['dataset-path-resolution', 'exact-revision-resolution', 'repository-metadata', 'git-blob-reads', 'authorized-lfs-download-actions']}}

    def resolve_dataset(self, locator_path):
        if not locator_path.startswith('lab/images'):
            raise ApiError('Missing', status=404, code='dataset_not_found')
        return 200, {'id': 'dataset-id', 'path': 'lab/images', 'repository_path': locator_path.removeprefix('lab/images').strip('/')}

    def resolve_revision(self, dataset_id, *, revision=None):
        self.revision_resolutions.append((dataset_id, revision))
        return 200, {'dataset_id': dataset_id, 'requested_revision': revision or 'main', 'resolved_commit': COMMIT}

    def get_repository_blob(self, dataset_id, *, revision, path):
        if path == 'large.bin':
            return 200, {'object_id': 'b' * 40, 'size': 130, 'is_lfs': True, 'lfs_object_id': LFS_OID, 'lfs_size': len(LFS_CONTENT)}
        if path in FILES:
            return 200, {'object_id': 'c' * 40, 'size': len(FILES[path]), 'is_lfs': False, 'lfs_object_id': None, 'lfs_size': None}
        raise ApiError('Missing', status=404, code='path_not_found')

    def list_repository_tree(self, dataset_id, *, revision, path='', limit=100, offset=0):
        self.tree_requests.append((dataset_id, revision, path, limit, offset))
        if path == 'batch':
            items = [
                {'path': 'batch/first.txt', 'object_type': 'blob'},
                {'path': 'batch/nested', 'object_type': 'tree'},
            ]
        elif path == 'batch/nested':
            items = [{'path': 'batch/nested/second.txt', 'object_type': 'blob'}]
        else:
            raise ApiError('Missing', status=404, code='path_not_found')
        return 200, {'items': items, 'next_offset': None}

    def authorize_repository_download(self, dataset_id, *, revision, path):
        self.authorizations.append((dataset_id, revision, path))
        if path == 'large.bin':
            return 200, _action(path=path, revision=revision, content=LFS_CONTENT, storage='lfs', lfs_object_id=LFS_OID)
        return 200, _action(path=path, revision=revision, content=FILES[path], storage='git')


def _action(*, path, revision, content, storage, lfs_object_id=None):
    """Build one exact transfer action."""

    authority = 'objects.example.test' if storage == 'lfs' else 'data.example.test'
    return {
        'resolved_commit': revision,
        'path': path,
        'size': len(content),
        'object_id': 'd' * 40,
        'lfs_object_id': lfs_object_id,
        'storage': storage,
        'method': 'GET',
        'url': f'https://{authority}/{path}',
        'headers': {},
        'expires_in': 300 if storage == 'lfs' else None,
        'range_supported': True,
    }


class TransferResponse:
    """Provide the bounded response surface used by the filesystem."""

    def __init__(self, content, *, start, end, size):
        self.status = 206
        self.headers = {'Content-Range': f'bytes {start}-{end}/{size}'}
        self.content = content

    def read(self, amount):
        return self.content[:amount]

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception, traceback):
        return False


class DownloadOpener:
    """Serve exact ranges and record transfer behavior."""

    def __init__(self, *, lfs_content=LFS_CONTENT):
        self.lfs_content = lfs_content
        self.requests = []

    def open(self, request, timeout):
        path = request.full_url.split('.test/', 1)[1]
        content = self.lfs_content if path == 'large.bin' else FILES[path]
        start, end = _parse_range(request.get_header('Range'))
        self.requests.append((path, start, end, request.get_header('Authorization')))
        return TransferResponse(content[start : end + 1], start=start, end=end, size=len(content))


class InterruptedOpener(DownloadOpener):
    """Fail after one completed range to simulate an interrupted transfer."""

    def open(self, request, timeout):
        if self.requests:
            raise URLError('simulated disconnect')
        return super().open(request, timeout)


def _parse_range(header):
    """Parse the bounded range emitted by the filesystem."""

    start, end = header.removeprefix('bytes=').split('-', 1)
    return int(start), int(end)


class FileSystemDownloadTests(unittest.TestCase):
    """Verify atomic, pinned, and integrity-checked downloads."""

    def setUp(self):
        """Reset API observations before each test."""

        DownloadApi.instances = []
        self.opener = DownloadOpener()
        self.fs = NiyanFileSystem(token='niyan_test-token', api_factory=DownloadApi, transfer_opener=self.opener, block_size=7)

    def test_lfs_download_streams_then_atomically_publishes_verified_content(self):
        """Transfer bounded chunks and expose only the verified final file."""

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'large.bin'

            self.fs.get_file('niyan://data.example.test/lab/images/large.bin?revision=main', destination)

            self.assertEqual(destination.read_bytes(), LFS_CONTENT)
            self.assertFalse(Path(f'{destination}.niyan-part').exists())
            self.assertGreater(len(self.opener.requests), 1)
            self.assertTrue(all(request[3] is None for request in self.opener.requests))

    def test_integrity_failure_preserves_existing_destination_and_partial_by_default(self):
        """Never publish corrupt LFS bytes over a previously complete file."""

        opener = DownloadOpener(lfs_content=b'x' * len(LFS_CONTENT))
        fs = NiyanFileSystem(token='niyan_test-token', api_factory=DownloadApi, transfer_opener=opener, block_size=7)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'large.bin'
            destination.write_bytes(b'previous complete file')

            with self.assertRaises(NiyanIntegrityError):
                fs.get_file('niyan://data.example.test/lab/images/large.bin?revision=main', destination)

            self.assertEqual(destination.read_bytes(), b'previous complete file')
            self.assertEqual(Path(f'{destination}.niyan-part').read_bytes(), b'x' * len(LFS_CONTENT))

    def test_failed_download_can_remove_partial_and_refuse_overwrite(self):
        """Honor explicit cleanup and no-overwrite policies before publication."""

        opener = DownloadOpener(lfs_content=b'x' * len(LFS_CONTENT))
        fs = NiyanFileSystem(token='niyan_test-token', api_factory=DownloadApi, transfer_opener=opener, block_size=7)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'large.bin'
            destination.write_bytes(b'existing')

            with self.assertRaises(FileExistsError):
                fs.get_file('niyan://data.example.test/lab/images/large.bin?revision=main', destination, overwrite=False)
            self.assertEqual(opener.requests, [])

            destination.unlink()
            with self.assertRaises(NiyanIntegrityError):
                fs.get_file('niyan://data.example.test/lab/images/large.bin?revision=main', destination, keep_partial=False)
            self.assertFalse(destination.exists())
            self.assertFalse(Path(f'{destination}.niyan-part').exists())

    def test_interrupted_download_never_publishes_the_final_path(self):
        """Leave only a recognizable partial artifact after transport failure."""

        opener = InterruptedOpener()
        fs = NiyanFileSystem(token='niyan_test-token', api_factory=DownloadApi, transfer_opener=opener, block_size=7)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'large.bin'

            with self.assertRaises(OSError):
                fs.get_file('niyan://data.example.test/lab/images/large.bin?revision=main', destination)

            self.assertFalse(destination.exists())
            self.assertEqual(Path(f'{destination}.niyan-part').read_bytes(), LFS_CONTENT[:7])

    def test_recursive_download_resolves_once_and_pins_every_child(self):
        """Materialize a directory without re-resolving a moving branch."""

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'download'

            self.fs.get('niyan://data.example.test/lab/images/batch?revision=main', destination, recursive=True)

            self.assertEqual((destination / 'first.txt').read_bytes(), FILES['batch/first.txt'])
            self.assertEqual((destination / 'nested' / 'second.txt').read_bytes(), FILES['batch/nested/second.txt'])
            api = DownloadApi.instances[-1]
            self.assertEqual(api.revision_resolutions, [('dataset-id', 'main')])
            self.assertTrue(all(request[1] == COMMIT for request in api.tree_requests))
            self.assertEqual(api.authorizations, [('dataset-id', COMMIT, 'batch/first.txt'), ('dataset-id', COMMIT, 'batch/nested/second.txt')])


if __name__ == '__main__':
    unittest.main()
