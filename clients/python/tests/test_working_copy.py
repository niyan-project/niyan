import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from niyan.cli import build_parser
from niyan.config import CheckoutIdentity, Configuration, find_local_config
from niyan.errors import GitError
from niyan.working_copy import parse_porcelain_v2, show_diff, show_log, show_status


DATASET_ID = '22222222-2222-2222-2222-222222222222'
DATASET_HOST = 'https://niyan.example'
DATASET_PATH = 'researcher/images'
LFS_OBJECT_ID = 'a' * 64
LFS_POINTER = f'version https://git-lfs.github.com/spec/v1\noid sha256:{LFS_OBJECT_ID}\nsize 123\n'


class WorkingCopyTests(unittest.TestCase):
    """Exercise read-only commands against ordinary Git repositories."""

    def setUp(self):
        """Create an isolated, configured Niyān dataset checkout."""

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / 'dataset'
        self._initialize_checkout(self.repository)
        (self.repository / '.gitattributes').write_text('*.bin filter=lfs diff=lfs merge=lfs -text\n')
        (self.repository / 'README.md').write_text('# Images\n')
        (self.repository / 'large.bin').write_text(LFS_POINTER)
        self._git('add', '.gitattributes', 'README.md', 'large.bin')
        self._git('commit', '-m', 'Initial dataset')

    def tearDown(self):
        """Remove the isolated checkout."""

        self.temporary_directory.cleanup()

    def test_status_reports_identity_branch_and_unavailable_lfs_content(self):
        """Describe clean checkout identity and an unmaterialized LFS pointer."""

        output = io.StringIO()

        status = show_status(cwd=self.repository, stdout=output)

        self.assertEqual(status['branch'], 'main')
        self.assertEqual(status['entries'], [])
        self.assertIn(f'Dataset: {DATASET_PATH} ({DATASET_ID})', output.getvalue())
        self.assertIn('Branch: main', output.getvalue())
        self.assertIn('History: full', output.getvalue())
        self.assertIn('Git working tree clean.', output.getvalue())
        self.assertIn('content unavailable: large.bin', output.getvalue())

    def test_status_distinguishes_renames_untracked_files_and_materialized_lfs_content(self):
        """Render Git state and LFS materialization without format inspection."""

        self._git('mv', 'README.md', 'NOTES.md')
        (self.repository / 'scratch.txt').write_text('untracked\n')
        (self.repository / 'large.bin').write_bytes(b'materialized large-file content')
        output = io.StringIO()

        show_status(cwd=self.repository, stdout=output)

        rendered = output.getvalue()
        self.assertIn('R. Renamed (staged): README.md -> NOTES.md', rendered)
        self.assertIn('?? Untracked: scratch.txt', rendered)
        self.assertIn('.M Modified (unstaged): large.bin [LFS: materialized]', rendered)

    def test_status_recognizes_a_cached_lfs_pointer(self):
        """Distinguish a local LFS object from unavailable pointer content."""

        cached_object = self.repository / '.git' / 'lfs' / 'objects' / 'aa' / 'aa' / LFS_OBJECT_ID
        cached_object.parent.mkdir(parents=True)
        cached_object.write_bytes(b'cached object')
        output = io.StringIO()

        show_status(cwd=self.repository, stdout=output)

        self.assertIn('pointer cached: large.bin', output.getvalue())

    def test_diff_streams_unstaged_and_staged_git_output(self):
        """Preserve Git's ordinary staged-versus-unstaged distinction."""

        (self.repository / 'README.md').write_text('# Updated images\n')
        with tempfile.TemporaryFile() as stream:
            show_diff(cwd=self.repository, stdout=stream)
            stream.seek(0)
            unstaged = stream.read().decode()
        self.assertIn('-# Images', unstaged)
        self.assertIn('+# Updated images', unstaged)

        self._git('add', 'README.md')
        with tempfile.TemporaryFile() as stream:
            show_diff(cwd=self.repository, staged=True, stdout=stream)
            stream.seek(0)
            staged = stream.read().decode()
        self.assertIn('-# Images', staged)
        self.assertIn('+# Updated images', staged)

    def test_log_is_bounded_and_handles_an_unborn_repository(self):
        """Limit history output and describe a checkout with no commits."""

        (self.repository / 'README.md').write_text('# New revision\n')
        self._git('add', 'README.md')
        self._git('commit', '-m', 'Second revision')
        output = io.StringIO()

        show_log(cwd=self.repository, limit=1, stdout=output)

        self.assertIn('Second revision', output.getvalue())
        self.assertNotIn('Initial dataset', output.getvalue())

        empty_repository = self.root / 'empty'
        self._initialize_checkout(empty_repository)
        empty_output = io.StringIO()
        show_log(cwd=empty_repository, stdout=empty_output)
        self.assertIn('No commits.', empty_output.getvalue())
        empty_status_output = io.StringIO()
        show_status(cwd=empty_repository, stdout=empty_status_output)
        self.assertIn('Git working tree clean.', empty_status_output.getvalue())

    def test_commands_reject_a_checkout_with_an_inconsistent_remote(self):
        """Validate immutable checkout identity before reading Git state."""

        self._git('remote', 'set-url', 'origin', 'https://niyan.example/git/33333333-3333-3333-3333-333333333333.git')

        with self.assertRaisesRegex(GitError, 'does not match'):
            show_status(cwd=self.repository, stdout=io.StringIO())

    def test_porcelain_parser_preserves_unusual_paths_and_conflicts(self):
        """Parse NUL-delimited paths without line-based ambiguity."""

        payload = (
            b'# branch.head main\0'
            b'# branch.upstream origin/main\0'
            b'# branch.ab +2 -1\0'
            b'2 R. N... 100644 100644 100644 aaaaaaa bbbbbbb R100 new\nname.txt\0old name.txt\0'
            b'u UU N... 100644 100644 100644 100644 aaaaaaa bbbbbbb ccccccc conflict.txt\0'
            b'? new file.txt\0'
        )

        status = parse_porcelain_v2(payload)

        self.assertEqual(status['branch'], 'main')
        self.assertEqual(status['upstream'], 'origin/main')
        self.assertEqual((status['ahead'], status['behind']), (2, 1))
        self.assertEqual(status['entries'][0]['path'], 'new\nname.txt')
        self.assertEqual(status['entries'][0]['original_path'], 'old name.txt')
        self.assertEqual(status['entries'][1]['code'], 'UU')
        self.assertEqual(status['entries'][2]['code'], '??')

    def test_status_escapes_control_characters_in_paths(self):
        """Prevent an unusual filename from forging another output line."""

        (self.repository / 'line\nbreak.txt').write_text('untracked\n')
        output = io.StringIO()

        show_status(cwd=self.repository, stdout=output)

        self.assertIn(r'line\nbreak.txt', output.getvalue())
        self.assertNotIn('line\nbreak.txt', output.getvalue())

    def test_cli_accepts_positive_log_limits(self):
        """Expose a positive bounded history option at the public CLI surface."""

        arguments = build_parser().parse_args(['log', '--limit', '5'])

        self.assertEqual(arguments.limit, 5)
        with self.assertRaises(SystemExit):
            build_parser().parse_args(['log', '--limit', '0'])

    def _initialize_checkout(self, path):
        """Initialize a Git tree with matching checkout-private identity."""

        path.mkdir()
        subprocess.run(['git', 'init', '--initial-branch=main', str(path)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(path), 'config', 'user.name', 'Niyān Test'], check=True)
        subprocess.run(['git', '-C', str(path), 'config', 'user.email', 'test@niyan.example'], check=True)
        subprocess.run(['git', '-C', str(path), 'remote', 'add', 'origin', f'{DATASET_HOST}/git/{DATASET_ID}.git'], check=True)
        configuration = Configuration()
        configuration.set_checkout(CheckoutIdentity(host=DATASET_HOST, dataset_id=DATASET_ID, dataset_path=DATASET_PATH, history='shallow'))
        configuration.save(find_local_config(path, required=True))

    def _git(self, *arguments):
        """Run one required Git command in the primary checkout."""

        return subprocess.run(['git', '-C', str(self.repository), *arguments], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
