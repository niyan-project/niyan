import io
import subprocess
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from django.utils import timezone

from datasets.browser_commits import BrowserCommitUnavailable, BrowserDraftInvalid, _attribute_updates, _base_entries, _has_branches, _hash_stream, _object_id_length, _optional_ref, _quote_attribute_pattern, _read_index_blob, _repository, _require_open_draft, _resulting_paths, _run_git, _validate_branch, _validate_resulting_paths, validate_repository_path
from datasets.models import BrowserCommitChange, BrowserCommitDraft
from datasets.repositories import RepositoryReadError


class FakeHashProcess:
    """Provide the subprocess pipes used by streamed Git hashing."""

    def __init__(self, *, oid=b'a' * 40, returncode=0):
        """Initialize output, return status, and process-state recording."""

        class RecordingInput(io.BytesIO):
            """Retain written bytes after the code closes the pipe."""

            def close(self):
                """Keep the in-memory buffer inspectable."""

        self.stdin = RecordingInput()
        self.stdout = io.BytesIO(oid + b'\n')
        self.returncode = returncode
        self.killed = False

    def kill(self):
        """Record forced termination."""

        self.killed = True

    def wait(self, timeout=None):
        """Return the configured process status."""

        return self.returncode


class BrowserCommitHelperTests(SimpleTestCase):
    """Verify browser Git plumbing validation and sanitized failure behavior."""

    def test_repository_paths_reject_unsafe_components_and_controls(self):
        """Accept one POSIX path while rejecting traversal and Git internals."""

        self.assertEqual(validate_repository_path('data/file.csv'), 'data/file.csv')
        invalid = [None, '', 'x' * 4097, '/absolute', 'trailing/', 'null\x00byte', 'data//file', 'data/./file', 'data/../file', '.GIT/config', 'data/\x1ffile', 'data/\x7ffile']
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(BrowserDraftInvalid):
                validate_repository_path(value)

    def test_open_draft_validation_rejects_terminal_and_expired_state(self):
        """Permit only open, unexpired drafts."""

        valid = SimpleNamespace(state=BrowserCommitDraft.State.OPEN, expires_at=timezone.now() + timedelta(minutes=1))
        self.assertIs(_require_open_draft(valid), valid)
        for draft in (SimpleNamespace(state=BrowserCommitDraft.State.COMMITTED, expires_at=valid.expires_at), SimpleNamespace(state=BrowserCommitDraft.State.OPEN, expires_at=timezone.now() - timedelta(seconds=1))):
            with self.subTest(state=draft.state), self.assertRaises(BrowserDraftInvalid):
                _require_open_draft(draft)

    def test_repository_and_branch_resolution_map_git_failures(self):
        """Translate unsafe storage and invalid Git branch grammar."""

        store = Mock()
        store.existing_path.side_effect = RepositoryReadError('private')
        with patch('datasets.browser_commits.GitRepositoryStore', return_value=store), self.assertRaises(BrowserCommitUnavailable):
            _repository('dataset-id')

        for branch in (None, '', 'refs/heads/main', 'x' * 256):
            with self.subTest(branch=branch), self.assertRaises(BrowserDraftInvalid):
                _validate_branch(branch, '/repo')
        with patch('datasets.browser_commits._run_git', return_value=SimpleNamespace(returncode=1)), self.assertRaises(BrowserDraftInvalid):
            _validate_branch('invalid..name', '/repo')
        with patch('datasets.browser_commits._run_git', return_value=SimpleNamespace(returncode=0)):
            self.assertEqual(_validate_branch('main', '/repo'), 'main')

    def test_optional_refs_and_branch_detection_use_git_results(self):
        """Distinguish missing refs and empty repositories."""

        with patch('datasets.browser_commits._run_git', return_value=SimpleNamespace(returncode=0, stdout=b'a' * 40 + b'\n')):
            self.assertEqual(_optional_ref('/repo', 'refs/heads/main'), 'a' * 40)
        with patch('datasets.browser_commits._run_git', return_value=SimpleNamespace(returncode=1, stdout=b'')):
            self.assertIsNone(_optional_ref('/repo', 'refs/heads/main'))
            self.assertFalse(_has_branches('/repo'))
        with patch('datasets.browser_commits._run_git', return_value=SimpleNamespace(stdout=b'refs/heads/main\n')):
            self.assertTrue(_has_branches('/repo'))

    def test_stream_hashing_enforces_declared_size_and_valid_oid(self):
        """Reject truncated, oversized, unavailable, and malformed hash output."""

        process = FakeHashProcess()
        with patch('datasets.browser_commits.subprocess.Popen', return_value=process):
            self.assertEqual(_hash_stream(repository='/repo', stream=io.BytesIO(b'data'), size=4), 'a' * 40)
        self.assertEqual(process.stdin.getvalue(), b'data')

        for stream, size, message in ((io.BytesIO(b'ab'), 4, 'ended before'), (io.BytesIO(b'abcde'), 4, 'exceeds')):
            process = FakeHashProcess()
            with self.subTest(message=message), patch('datasets.browser_commits.subprocess.Popen', return_value=process), self.assertRaisesRegex(BrowserDraftInvalid, message):
                _hash_stream(repository='/repo', stream=stream, size=size)
            self.assertTrue(process.killed)

        with patch('datasets.browser_commits.subprocess.Popen', side_effect=OSError('private')), self.assertRaises(BrowserCommitUnavailable):
            _hash_stream(repository='/repo', stream=io.BytesIO(), size=0)
        for process in (FakeHashProcess(returncode=1), FakeHashProcess(oid=b'not-an-object-id')):
            with patch('datasets.browser_commits.subprocess.Popen', return_value=process), self.assertRaises(BrowserCommitUnavailable):
                _hash_stream(repository='/repo', stream=io.BytesIO(), size=0)

    def test_base_and_resulting_paths_apply_draft_operations(self):
        """Parse recursive trees and apply upsert/delete operations exactly."""

        self.assertEqual(_base_entries('/repo', ''), {})
        output = b'100644 blob ' + b'a' * 40 + b'\tkeep.txt\x00100644 blob ' + b'b' * 40 + b'\tdelete.txt\x00'
        with patch('datasets.browser_commits._run_git', return_value=SimpleNamespace(stdout=output)):
            self.assertEqual(_base_entries('/repo', 'commit'), {'keep.txt': 'blob', 'delete.txt': 'blob'})

        changes = [SimpleNamespace(path='delete.txt', operation=BrowserCommitChange.Operation.DELETE), SimpleNamespace(path='new.txt', operation=BrowserCommitChange.Operation.UPSERT)]
        draft = SimpleNamespace(dataset_id='dataset-id', base_commit='commit', changes=SimpleNamespace(only=Mock(return_value=changes)))
        with patch('datasets.browser_commits._repository', return_value='/repo'), patch('datasets.browser_commits._base_entries', return_value={'keep.txt': 'blob', 'delete.txt': 'blob'}):
            self.assertEqual(_resulting_paths(draft), {'keep.txt', 'new.txt'})

        with patch('datasets.browser_commits._resulting_paths', return_value={'folder'}), self.assertRaisesRegex(BrowserDraftInvalid, 'collide'):
            _validate_resulting_paths(draft=draft, proposed=('folder/file.txt', BrowserCommitChange.Operation.UPSERT))
        with patch('datasets.browser_commits._resulting_paths', return_value={'folder/file.txt'}):
            _validate_resulting_paths(draft=draft, proposed=('folder/file.txt', BrowserCommitChange.Operation.DELETE))

    def test_attribute_helpers_group_quote_and_read_index_content(self):
        """Create exact nearest-directory LFS patterns and inspect staged blobs."""

        self.assertEqual(_attribute_updates(['root.bin', 'raw/a.bin', 'raw/b.bin']), {'.gitattributes': ['root.bin'], 'raw/.gitattributes': ['a.bin', 'b.bin']})
        self.assertEqual(_quote_attribute_pattern('a "quoted" \\ file.bin'), '"/a \\"quoted\\" \\\\ file.bin"')
        with patch('datasets.browser_commits._run_git', return_value=SimpleNamespace(stdout=b'')):
            self.assertEqual(_read_index_blob('/repo', {}, '.gitattributes'), b'')
        with patch('datasets.browser_commits._run_git', return_value=SimpleNamespace(stdout=b'invalid')):
            with self.assertRaises(BrowserCommitUnavailable):
                _read_index_blob('/repo', {}, '.gitattributes')
        with patch('datasets.browser_commits._run_git', side_effect=(SimpleNamespace(stdout=b'100644 ' + b'a' * 40 + b' 0\t.gitattributes\x00'), SimpleNamespace(stdout=b'content\n'))):
            self.assertEqual(_read_index_blob('/repo', {}, '.gitattributes'), b'content\n')

    def test_object_format_and_git_runner_failure_mapping(self):
        """Default unknown formats to SHA-1 width and sanitize process errors."""

        for value, expected in ((b'sha1\n', 40), (b'sha256\n', 64), (b'unknown\n', 40)):
            with self.subTest(value=value), patch('datasets.browser_commits._run_git', return_value=SimpleNamespace(stdout=value)):
                self.assertEqual(_object_id_length('/repo'), expected)
        with patch('datasets.browser_commits.subprocess.run', side_effect=OSError('private')), self.assertRaises(BrowserCommitUnavailable):
            _run_git('/repo', ['status'])
        with patch('datasets.browser_commits.subprocess.run', return_value=SimpleNamespace(returncode=1)), self.assertRaises(BrowserCommitUnavailable):
            _run_git('/repo', ['status'])
        result = SimpleNamespace(returncode=1)
        with patch('datasets.browser_commits.subprocess.run', return_value=result):
            self.assertIs(_run_git('/repo', ['status'], check=False), result)


if __name__ == '__main__':
    unittest.main()
