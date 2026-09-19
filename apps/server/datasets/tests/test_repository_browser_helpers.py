import io
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from datasets.repositories import RepositoryReadError
from datasets.repository_browser import BlobMetadata, InvalidRepositoryInput, LfsContentUnavailable, RepositoryBrowseError, RepositoryBrowser, RepositoryPathNotFound, _normalize_path, _parse_lfs_pointer, _stream_process, _stream_process_range, _validate_component


class FakeProcess:
    """Provide streaming process output and lifecycle controls."""

    def __init__(self, content=b'', *, returncode=0, running=True):
        """Initialize output and process state."""

        self.stdout = io.BytesIO(content)
        self.returncode = returncode
        self.running = running
        self.terminated = False
        self.killed = False

    def poll(self):
        """Return ``None`` while configured as running."""

        return None if self.running else self.returncode

    def terminate(self):
        """Record graceful termination."""

        self.terminated = True
        self.running = False

    def kill(self):
        """Record forced termination."""

        self.killed = True
        self.running = False

    def wait(self, timeout=None):
        """Return the configured status."""

        return self.returncode


class RepositoryBrowserHelperTests(SimpleTestCase):
    """Verify bounded Git parsing and streaming failure behavior."""

    def make_browser(self):
        """Construct a browser without touching repository storage."""

        browser = object.__new__(RepositoryBrowser)
        browser.dataset = SimpleNamespace(id='dataset-id')
        browser.repository_path = '/repo'
        return browser

    def test_constructor_translates_unsafe_repository_storage(self):
        """Hide filesystem details when a repository cannot be opened."""

        store = Mock()
        store.existing_path.side_effect = RepositoryReadError('private')
        with self.assertRaises(RepositoryBrowseError):
            RepositoryBrowser(SimpleNamespace(id='dataset-id'), repository_store=store)

    def test_refs_commits_and_tree_reject_malformed_git_output(self):
        """Fail closed around unsupported kinds and malformed metadata."""

        browser = self.make_browser()
        with self.assertRaises(InvalidRepositoryInput):
            browser.list_refs(kind='notes', limit=10, offset=0)

        browser.resolve_revision = Mock(return_value='a' * 40)
        browser._run = Mock(return_value=SimpleNamespace(stdout=b'only\x00five\x00fields\x00here\x00bad'))
        with self.assertRaisesRegex(RepositoryBrowseError, 'malformed commit'):
            browser.list_commits(revision='main', limit=10, offset=0)

        browser._read_nul_records = Mock(return_value=[b'malformed tree entry'])
        with self.assertRaisesRegex(RepositoryBrowseError, 'malformed tree'):
            browser.list_tree(revision='main', path='', limit=10, offset=0)

    def test_blob_metadata_rejects_missing_objects_and_non_files(self):
        """Distinguish absent paths from tree objects."""

        browser = self.make_browser()
        browser.resolve_revision = Mock(return_value='a' * 40)
        browser._run = Mock(return_value=SimpleNamespace(returncode=1, stdout=b''))
        with self.assertRaises(RepositoryPathNotFound):
            browser.get_blob_metadata(revision='main', path='missing')

        browser._run = Mock(side_effect=(SimpleNamespace(returncode=0, stdout=b'b' * 40 + b'\n'), SimpleNamespace(returncode=0, stdout=b'tree\n')))
        with self.assertRaisesRegex(RepositoryPathNotFound, 'not a file'):
            browser.get_blob_metadata(revision='main', path='directory')
        self.assertEqual(browser._get_lfs_metadata(object_id='b' * 40, size=2048), (None, None))
        self.assertEqual(browser._get_lfs_metadata(object_id='b' * 40, size=None), (None, None))

    def test_batch_pointer_parser_rejects_truncated_and_inconsistent_output(self):
        """Require each batch header, identity, size, type, and delimiter."""

        browser = self.make_browser()
        object_id = 'a' * 40
        cases = [
            b'no newline',
            b'invalid header\ncontent\n',
            f'{"b" * 40} blob 3\nabc\n'.encode(),
            f'{object_id} tree 3\nabc\n'.encode(),
            f'{object_id} blob 4\nabc\n'.encode(),
            f'{object_id} blob 3\nabcX'.encode(),
        ]
        for output in cases:
            with self.subTest(output=output), patch.object(browser, '_run', return_value=SimpleNamespace(stdout=output)), self.assertRaises(RepositoryBrowseError):
                browser._get_lfs_metadata_batch([(object_id, 3)])

    def test_open_blob_and_range_reject_lfs_and_invalid_offsets(self):
        """Keep LFS bytes out of Django and validate exact stream bounds."""

        browser = self.make_browser()
        lfs = BlobMetadata(path='data.bin', object_id='b' * 40, size=7, lfs_object_id='c' * 64, lfs_size=100)
        browser.get_blob_metadata = Mock(return_value=('a' * 40, lfs))
        with self.assertRaises(LfsContentUnavailable):
            browser.open_blob(revision='main', path='data.bin')
        with self.assertRaises(LfsContentUnavailable):
            browser.open_blob_range(revision='main', path='data.bin', start=0, end=1)

        ordinary = BlobMetadata(path='data.bin', object_id='b' * 40, size=7, lfs_object_id=None, lfs_size=None)
        browser.get_blob_metadata = Mock(return_value=('a' * 40, ordinary))
        with self.assertRaises(InvalidRepositoryInput):
            browser.open_blob_range(revision='main', path='data.bin', start=5, end=8)

    def test_readme_search_handles_missing_and_unavailable_content(self):
        """Search conventional names and refuse oversized or LFS README files."""

        browser = self.make_browser()
        browser.resolve_revision = Mock(return_value='a' * 40)
        browser.get_blob_metadata = Mock(side_effect=RepositoryPathNotFound('missing'))
        with self.assertRaisesRegex(RepositoryPathNotFound, 'no root README'):
            browser.read_readme(revision='main')

        metadata = BlobMetadata(path='README.md', object_id='b' * 40, size=8, lfs_object_id='c' * 64, lfs_size=20)
        browser.get_blob_metadata = Mock(return_value=('a' * 40, metadata))
        with self.assertRaises(LfsContentUnavailable):
            browser.read_readme(revision='main')

    def test_run_and_popen_translate_process_and_output_failures(self):
        """Bound captured output and sanitize Git process errors."""

        browser = self.make_browser()
        with patch('datasets.repository_browser.subprocess.run', side_effect=OSError('private')), self.assertRaises(RepositoryBrowseError):
            browser._run(['status'])
        with patch('datasets.repository_browser.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=b'too long')), self.assertRaisesRegex(RepositoryBrowseError, 'exceeds'):
            browser._run(['status'], maximum_output=2)
        with patch('datasets.repository_browser.subprocess.run', return_value=SimpleNamespace(returncode=1, stdout=b'')), self.assertRaises(RepositoryBrowseError):
            browser._run(['status'])
        result = SimpleNamespace(returncode=1, stdout=b'')
        with patch('datasets.repository_browser.subprocess.run', return_value=result):
            self.assertIs(browser._run(['status'], check=False), result)
        with patch('datasets.repository_browser.subprocess.Popen', side_effect=OSError('private')), self.assertRaises(RepositoryBrowseError):
            browser._popen(['cat-file'])

    def test_nul_reader_terminates_at_bound_and_maps_process_failures(self):
        """Stop after the requested records and sanitize read/wait failures."""

        browser = self.make_browser()
        process = FakeProcess(b'one\x00two\x00three\x00')
        browser._popen = Mock(return_value=process)
        self.assertEqual(browser._read_nul_records(['ls-tree'], maximum=2, missing_error=RepositoryPathNotFound), [b'one', b'two'])
        self.assertTrue(process.terminated)

        process = FakeProcess(b'', returncode=1, running=False)
        browser._popen = Mock(return_value=process)
        with self.assertRaises(RepositoryPathNotFound):
            browser._read_nul_records(['ls-tree'], maximum=2, missing_error=RepositoryPathNotFound)

        process = FakeProcess(b'one\x00')
        process.wait = Mock(side_effect=(subprocess.TimeoutExpired('git', 5), 1))
        browser._popen = Mock(return_value=process)
        with self.assertRaises(RepositoryBrowseError):
            browser._read_nul_records(['ls-tree'], maximum=2, missing_error=RepositoryPathNotFound)
        self.assertTrue(process.killed)

    def test_path_pointer_and_stream_helpers_cover_edge_cases(self):
        """Validate paths, pointers, and child-process stream termination."""

        self.assertEqual(_normalize_path('/data/files/', allow_empty=True), 'data/files')
        for value, allow_empty in (('', False), ('data//file', True), ('data/../file', True)):
            with self.subTest(value=value), self.assertRaises(InvalidRepositoryInput):
                _normalize_path(value, allow_empty=allow_empty)
        for value in (None, 'x' * 5, '', 'bad\x1fvalue', 'bad\x7fvalue'):
            with self.subTest(value=value), self.assertRaises(InvalidRepositoryInput):
                _validate_component(value, allow_empty=False, maximum_length=4)

        oid = 'a' * 64
        self.assertEqual(_parse_lfs_pointer(f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize 12\n'), (oid, 12))
        for content in ('ordinary content', 'version https://git-lfs.github.com/spec/v1\noid bad\nsize 1', f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize invalid', f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize -1'):
            self.assertEqual(_parse_lfs_pointer(content), (None, None))

        process = FakeProcess(b'content')
        self.assertEqual(b''.join(_stream_process(process)), b'content')
        self.assertTrue(process.terminated)
        process = FakeProcess(b'abcdef')
        self.assertEqual(b''.join(_stream_process_range(process, start=2, end=5)), b'cde')
        self.assertTrue(process.terminated)
        for content, start, end in ((b'a', 2, 3), (b'ab', 0, 3)):
            process = FakeProcess(content)
            with self.assertRaises(RepositoryBrowseError):
                b''.join(_stream_process_range(process, start=start, end=end))


if __name__ == '__main__':
    unittest.main()
