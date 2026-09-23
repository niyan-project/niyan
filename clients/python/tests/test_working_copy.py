import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import niyan.working_copy as working_copy
from niyan.cli import build_parser, main
from niyan.config import CheckoutIdentity, Configuration, find_local_config
from niyan.errors import GitConflictError, GitError
from niyan.working_copy import LFS_POINTER_VERSION, LFS_SIZE_THRESHOLD, commit_changes, create_branch, create_tag, delete_branch, delete_tag, list_branches, list_tags, merge_revision, parse_porcelain_v2, rename_branch, restore_paths, show_diff, show_log, show_status, stage_paths, switch_branch


DATASET_ID = '22222222-2222-2222-2222-222222222222'
DATASET_HOST = 'https://niyan.example'
DATASET_PATH = 'researcher/images'
LFS_OBJECT_ID = 'a' * 64
LFS_POINTER = f'version https://git-lfs.github.com/spec/v1\noid sha256:{LFS_OBJECT_ID}\nsize 123\n'


class WorkingCopyTests(unittest.TestCase):
    """Exercise read-only commands against ordinary Git repositories."""

    def setUp(self):
        """Create an isolated, configured Niyān dataset checkout."""

        self.host_config = patch.dict(os.environ, {'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull})
        self.host_config.start()
        self.addCleanup(self.host_config.stop)
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / 'dataset'
        self.fake_bin = self.root / 'bin'
        self.fake_bin.mkdir()
        self.fake_lfs = self.fake_bin / 'git-lfs'
        self.fake_lfs.write_text(
            '#!/usr/bin/env python3\n'
            'import hashlib\n'
            'import pathlib\n'
            'import sys\n'
            'if len(sys.argv) > 1 and sys.argv[1] == "install":\n'
            '    pass\n'
            'elif len(sys.argv) > 1 and sys.argv[1] == "track":\n'
            '    filename = sys.argv[sys.argv.index("--filename") + 1]\n'
            '    attributes = pathlib.Path(".gitattributes")\n'
            '    existing = attributes.read_text() if attributes.exists() else ""\n'
            '    line = f"{filename} filter=lfs diff=lfs merge=lfs -text\\n"\n'
            '    if line not in existing:\n'
            '        attributes.write_text(existing + ("\\n" if existing and not existing.endswith("\\n") else "") + line)\n'
            'elif len(sys.argv) > 1 and sys.argv[1] == "clean":\n'
            '    content = sys.stdin.buffer.read()\n'
            '    if content.startswith(b"version https://git-lfs.github.com/spec/v1\\n"):\n'
            '        sys.stdout.buffer.write(content)\n'
            '    else:\n'
            '        oid = hashlib.sha256(content).hexdigest()\n'
            '        sys.stdout.write(f"version https://git-lfs.github.com/spec/v1\\noid sha256:{oid}\\nsize {len(content)}\\n")\n'
            'else:\n'
            '    raise SystemExit(2)\n'
        )
        self.fake_lfs.chmod(0o755)
        self._initialize_checkout(self.repository)
        (self.repository / '.gitattributes').write_text('*.bin filter=lfs diff=lfs merge=lfs -text\n')
        (self.repository / 'README.md').write_text('# Images\n')
        (self.repository / 'large.bin').write_text(LFS_POINTER)
        self._git('add', '.gitattributes', 'README.md', 'large.bin')
        self._git('commit', '-m', 'Initial dataset')
        self._git('config', 'filter.lfs.clean', f'{self.fake_lfs} clean')
        self._git('config', 'filter.lfs.smudge', 'cat')
        self._git('config', 'filter.lfs.required', 'true')

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

    def test_restore_preserves_the_staged_and_working_tree_distinction(self):
        """Restore content or unstage it according to the explicit mode."""

        (self.repository / 'README.md').write_text('# Unstaged revision\n')
        restore_paths(['README.md'], cwd=self.repository)
        self.assertEqual((self.repository / 'README.md').read_text(), '# Images\n')

        (self.repository / 'README.md').write_text('# Staged revision\n')
        self._git('add', 'README.md')
        restore_paths(['README.md'], staged=True, cwd=self.repository)
        self.assertEqual((self.repository / 'README.md').read_text(), '# Staged revision\n')
        self.assertEqual(self._git('diff', '--cached', '--name-only').stdout, b'')
        self.assertEqual(self._git('diff', '--name-only').stdout.strip(), b'README.md')

    def test_commit_creates_a_local_commit_and_rejects_an_empty_index(self):
        """Commit staged changes without synchronizing the remote."""

        (self.repository / 'README.md').write_text('# Committed revision\n')
        self._git('add', 'README.md')
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            commit_changes(message='Update dataset', cwd=self.repository, stdout=output, stderr=errors)

        self.assertEqual(self._git('log', '-1', '--pretty=%s').stdout.strip(), b'Update dataset')
        self.assertEqual(self._git('rev-list', '--count', 'HEAD').stdout.strip(), b'2')
        with self.assertRaisesRegex(GitError, 'no staged dataset changes'):
            commit_changes(message='Empty commit', cwd=self.repository)

    def test_commit_supports_the_first_commit_in_an_unborn_checkout(self):
        """Compare a staged initial tree against Git's implicit empty tree."""

        empty_repository = self.root / 'initial'
        self._initialize_checkout(empty_repository)
        (empty_repository / 'README.md').write_text('# Initial data\n')
        subprocess.run(['git', '-C', str(empty_repository), 'add', 'README.md'], check=True)

        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            commit_changes(message='Initial dataset', cwd=empty_repository, stdout=output, stderr=errors)

        subject = subprocess.run(['git', '-C', str(empty_repository), 'log', '-1', '--pretty=%s'], check=True, capture_output=True).stdout.strip()
        self.assertEqual(subject, b'Initial dataset')

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

    def test_cli_exposes_restore_and_commit_arguments(self):
        """Keep local write operations available through the public parser."""

        restore_arguments = build_parser().parse_args(['restore', '--staged', 'README.md', 'data/file.bin'])
        commit_arguments = build_parser().parse_args(['commit', '--message', 'Record data'])

        self.assertTrue(restore_arguments.staged)
        self.assertEqual(restore_arguments.paths, ['README.md', 'data/file.bin'])
        self.assertEqual(commit_arguments.message, 'Record data')

    def test_branch_workflow_delegates_to_safe_local_git_operations(self):
        """Create, list, switch, rename, and safely delete ordinary branches."""

        create_branch('experiment', start_point='main', cwd=self.repository)
        output = io.StringIO()
        list_branches(cwd=self.repository, stdout=output)
        self.assertIn('\texperiment\t', output.getvalue())
        self.assertIn('*\tmain\t', output.getvalue())

        with tempfile.TemporaryFile() as command_output, tempfile.TemporaryFile() as command_errors:
            switch_branch('experiment', cwd=self.repository, stdout=command_output, stderr=command_errors)
        rename_branch('experiment', 'analysis', cwd=self.repository)
        self.assertEqual(self._git('branch', '--show-current').stdout.strip(), b'analysis')
        with tempfile.TemporaryFile() as command_output, tempfile.TemporaryFile() as command_errors:
            switch_branch('scratch', create=True, start_point='main', cwd=self.repository, stdout=command_output, stderr=command_errors)
            switch_branch('main', cwd=self.repository, stdout=command_output, stderr=command_errors)

        class TerminalInput(io.StringIO):
            """Present deterministic confirmation text as an interactive terminal."""

            def isatty(self):
                """Report interactive input for deletion confirmation."""

                return True

        with self.assertRaisesRegex(GitError, 'cancelled'):
            delete_branch('analysis', cwd=self.repository, stdin=TerminalInput('wrong\n'), stdout=io.StringIO())
        delete_branch('analysis', cwd=self.repository, stdin=TerminalInput('analysis\n'), stdout=io.StringIO())
        delete_branch('scratch', cwd=self.repository, stdin=io.StringIO(), stdout=io.StringIO())
        self.assertNotIn(b'analysis', self._git('branch', '--list').stdout)

    def test_merge_preserves_git_conflicts_and_reports_whole_file_paths(self):
        """Complete clean merges while leaving conflicting files for the user."""

        create_branch('clean-change', start_point='main', cwd=self.repository)
        with tempfile.TemporaryFile() as command_output, tempfile.TemporaryFile() as command_errors:
            switch_branch('clean-change', cwd=self.repository, stdout=command_output, stderr=command_errors)
        (self.repository / 'clean.txt').write_text('clean\n')
        self._git('add', 'clean.txt')
        self._git('commit', '-m', 'Clean branch')
        with tempfile.TemporaryFile() as command_output, tempfile.TemporaryFile() as command_errors:
            switch_branch('main', cwd=self.repository, stdout=command_output, stderr=command_errors)
            merge_revision('clean-change', cwd=self.repository, stdout=command_output, stderr=command_errors)
        self.assertEqual((self.repository / 'clean.txt').read_text(), 'clean\n')

        create_branch('competing', start_point='main', cwd=self.repository)
        with tempfile.TemporaryFile() as command_output, tempfile.TemporaryFile() as command_errors:
            switch_branch('competing', cwd=self.repository, stdout=command_output, stderr=command_errors)
        (self.repository / 'README.md').write_text('# Their images\n')
        self._git('add', 'README.md')
        self._git('commit', '-m', 'Their change')
        with tempfile.TemporaryFile() as command_output, tempfile.TemporaryFile() as command_errors:
            switch_branch('main', cwd=self.repository, stdout=command_output, stderr=command_errors)
        (self.repository / 'README.md').write_text('# Our images\n')
        self._git('add', 'README.md')
        self._git('commit', '-m', 'Our change')

        with tempfile.TemporaryFile() as command_output, tempfile.TemporaryFile() as command_errors:
            with self.assertRaisesRegex(GitConflictError, 'whole-file conflicts in: README.md'):
                merge_revision('competing', cwd=self.repository, stdout=command_output, stderr=command_errors)
        self.assertTrue((self.repository / '.git' / 'MERGE_HEAD').exists())
        self.assertEqual(self._git('diff', '--name-only', '--diff-filter=U').stdout.strip(), b'README.md')
        self._git('merge', '--abort')

    def test_annotated_tag_workflow_lists_and_safely_deletes_tags(self):
        """Create annotated tags by default and preserve interactive deletion safety."""

        with tempfile.TemporaryFile() as command_output, tempfile.TemporaryFile() as command_errors:
            create_tag('v1', message='Dataset release', cwd=self.repository, stdout=command_output, stderr=command_errors)
        self.assertEqual(self._git('cat-file', '-t', 'refs/tags/v1').stdout.strip(), b'tag')
        output = io.StringIO()
        list_tags(cwd=self.repository, stdout=output)
        self.assertEqual(output.getvalue(), 'v1\n')
        delete_tag('v1', cwd=self.repository, stdin=io.StringIO(), stdout=io.StringIO())
        self.assertEqual(self._git('tag', '--list').stdout, b'')

    def test_cli_exposes_and_dispatches_branch_merge_and_tag_commands(self):
        """Connect the accepted grammar to each local Git wrapper."""

        branch_arguments = build_parser().parse_args(['branch', 'create', 'experiment', 'main'])
        switch_arguments = build_parser().parse_args(['switch', '--create', 'analysis', 'main'])
        merge_arguments = build_parser().parse_args(['merge', 'experiment'])
        tag_arguments = build_parser().parse_args(['tag', 'create', 'v1', '--message', 'Release'])
        self.assertEqual(branch_arguments.start_point, 'main')
        self.assertTrue(switch_arguments.create)
        self.assertEqual(merge_arguments.revision, 'experiment')
        self.assertEqual(tag_arguments.message, 'Release')

        with patch('niyan.cli.Path.cwd', return_value=self.repository), patch('niyan.cli.create_branch') as create_call:
            status = main(['branch', 'create', 'dispatch', 'main'])
        self.assertEqual(status, 0)
        create_call.assert_called_once_with('dispatch', start_point='main', cwd=self.repository)

    def test_cli_exposes_add_paths_all_and_mutually_exclusive_overrides(self):
        """Keep the accepted staging grammar available through the public CLI."""

        paths = build_parser().parse_args(['add', '--lfs', 'raw/image.dat'])
        everything = build_parser().parse_args(['add', '--all', '--verbose', '--git'])

        self.assertEqual(paths.paths, ['raw/image.dat'])
        self.assertTrue(paths.lfs)
        self.assertTrue(everything.all)
        self.assertTrue(everything.verbose)
        self.assertTrue(everything.git)
        with self.assertRaises(SystemExit):
            build_parser().parse_args(['add', '--lfs', '--git', 'data.bin'])

    def test_add_delegates_default_staging_to_git_attributes(self):
        """Avoid opening and classifying every file in a large selected tree."""

        (self.repository / 'small.txt').write_text('plain text\n')
        (self.repository / 'binary.dat').write_bytes(b'prefix\0suffix')
        (self.repository / 'exact.txt').write_bytes(b'a' * LFS_SIZE_THRESHOLD)
        (self.repository / 'large.txt').write_bytes(b'a' * (LFS_SIZE_THRESHOLD + 1))

        with patch('niyan.working_copy._inspect_candidate', side_effect=AssertionError('default add must not inspect files')):
            stage_paths(['small.txt', 'binary.dat', 'exact.txt', 'large.txt'], cwd=self.repository)

        self.assertEqual(self._git('show', ':small.txt').stdout, b'plain text\n')
        self.assertEqual(self._git('show', ':binary.dat').stdout, b'prefix\0suffix')
        self.assertEqual(self._git('show', ':exact.txt').stdout, b'a' * LFS_SIZE_THRESHOLD)
        self.assertEqual(self._git('show', ':large.txt').stdout, b'a' * (LFS_SIZE_THRESHOLD + 1))
        self.assertEqual((self.repository / '.gitattributes').read_text(), '*.bin filter=lfs diff=lfs merge=lfs -text\n')

    def test_add_verbose_streams_git_staging_output(self):
        """Delegate requested per-path output directly to Git."""

        (self.repository / 'visible.txt').write_text('show this path\n')

        with patch('niyan.working_copy._run_git', wraps=working_copy._run_git) as run_git:
            stage_paths([], all_paths=True, verbose=True, cwd=self.repository)

        _, arguments = run_git.call_args_list[-1].args[:2]
        self.assertEqual(arguments, ['add', '--verbose', '--all'])
        self.assertFalse(run_git.call_args_list[-1].kwargs['capture_output'])

    def test_add_all_stages_thousands_of_binary_files_without_classification(self):
        """Keep the default many-file path to one standard Git staging operation."""

        fixture = self.repository / 'many-files'
        fixture.mkdir()
        for index in range(3000):
            (fixture / f'image-{index:04d}.jpg').write_bytes(b'jpeg\0fixture')

        with patch('niyan.working_copy._inspect_candidate', side_effect=AssertionError('default add must not inspect files')):
            stage_paths([], all_paths=True, cwd=self.repository)

        staged = self._git('diff', '--cached', '--name-only').stdout.splitlines()
        self.assertEqual(sum(path.startswith(b'many-files/') for path in staged), 3000)

    def test_add_preserves_existing_git_and_lfs_storage_modes(self):
        """Do not silently migrate tracked paths when their current content changes."""

        (self.repository / 'growing.txt').write_text('small\n')
        self._git('add', 'growing.txt')
        self._git('commit', '-m', 'Add ordinary file')
        (self.repository / 'growing.txt').write_bytes(b'a' * (LFS_SIZE_THRESHOLD + 1))

        with self._lfs_environment():
            stage_paths(['growing.txt'], cwd=self.repository)

        self.assertEqual(int(self._git('cat-file', '-s', ':growing.txt').stdout), LFS_SIZE_THRESHOLD + 1)

        self._git('reset', '--hard', 'HEAD')
        (self.repository / 'preserved.dat').write_bytes(b'original\0content')
        with self._lfs_environment():
            stage_paths(['preserved.dat'], force_lfs=True, cwd=self.repository)
        self._git('commit', '-m', 'Add LFS file')
        (self.repository / 'preserved.dat').write_text('now small text\n')

        with self._lfs_environment():
            stage_paths(['preserved.dat'], cwd=self.repository)

        self.assertTrue(self._git('show', ':preserved.dat').stdout.startswith(LFS_POINTER_VERSION))
        self.assertIn('preserved.dat filter=lfs', (self.repository / '.gitattributes').read_text())

    def test_add_all_stages_additions_modifications_and_deletions(self):
        """Apply the same policy while delegating all-change selection to Git."""

        (self.repository / 'README.md').write_text('# Updated images\n')
        (self.repository / 'small.txt').write_text('new text\n')
        (self.repository / 'large.bin').unlink()

        stage_paths([], all_paths=True, cwd=self.repository)

        staged = self._git('diff', '--cached', '--name-status').stdout.splitlines()
        self.assertIn(b'M\tREADME.md', staged)
        self.assertIn(b'A\tsmall.txt', staged)
        self.assertIn(b'D\tlarge.bin', staged)

    def test_add_honors_attributes_and_explicit_overrides(self):
        """Give effective attributes and explicit modes their documented precedence."""

        (self.repository / '.gitattributes').write_text('*.forced filter=lfs diff=lfs merge=lfs -text\n*.plain -filter -diff -merge\n')
        (self.repository / 'small.forced').write_text('small text\n')
        (self.repository / 'binary.plain').write_bytes(b'binary\0content')
        (self.repository / 'explicit-lfs.txt').write_text('small text\n')
        (self.repository / 'explicit-git.bin').write_bytes(b'binary\0content')

        with self._lfs_environment():
            stage_paths(['.gitattributes', 'small.forced', 'binary.plain'], cwd=self.repository)
            stage_paths(['explicit-lfs.txt'], force_lfs=True, cwd=self.repository)
            stage_paths(['explicit-git.bin'], force_git=True, cwd=self.repository, stderr=io.StringIO())

        self.assertTrue(self._git('show', ':small.forced').stdout.startswith(LFS_POINTER_VERSION))
        self.assertEqual(self._git('show', ':binary.plain').stdout, b'binary\0content')
        self.assertTrue(self._git('show', ':explicit-lfs.txt').stdout.startswith(LFS_POINTER_VERSION))
        self.assertEqual(self._git('show', ':explicit-git.bin').stdout, b'binary\0content')
        self.assertEqual(self._git('check-attr', 'filter', '--', 'explicit-git.bin').stdout.strip(), b'explicit-git.bin: filter: unset')

    def test_add_persists_recursive_directory_overrides(self):
        """Represent explicit directory choices with ordinary recursive attribute rules."""

        lfs_directory = self.repository / 'lfs-data'
        git_directory = self.repository / 'git-data'
        lfs_directory.mkdir()
        git_directory.mkdir()
        (lfs_directory / 'small.txt').write_text('small text\n')
        (lfs_directory / '.gitattributes').write_text('# nested attributes\n')
        (git_directory / 'binary.bin').write_bytes(b'binary\0content')

        with self._lfs_environment():
            stage_paths(['lfs-data'], force_lfs=True, cwd=self.repository)
            stage_paths(['git-data'], force_git=True, cwd=self.repository)

        self.assertTrue(self._git('show', ':lfs-data/small.txt').stdout.startswith(LFS_POINTER_VERSION))
        self.assertEqual(self._git('show', ':lfs-data/.gitattributes').stdout, b'# nested attributes\n')
        self.assertEqual(self._git('show', ':git-data/binary.bin').stdout, b'binary\0content')
        attributes = (self.repository / '.gitattributes').read_text()
        self.assertIn('"lfs-data/**" filter=lfs diff=lfs merge=lfs -text', attributes)
        self.assertIn('"lfs-data/.gitattributes" -filter -diff -merge', attributes)
        self.assertIn('"git-data/**" -filter -diff -merge', attributes)

    def test_add_uses_current_attributes_for_a_filesystem_rename(self):
        """Let Git decide storage after a path moves outside its former LFS rule."""

        source = self.repository / 'source.txt'
        source.write_text('small LFS text\n')
        with self._lfs_environment():
            stage_paths(['source.txt'], force_lfs=True, cwd=self.repository)
        self._git('commit', '-m', 'Add explicit LFS text')
        (self.repository / '.gitattributes').write_text('*.bin filter=lfs diff=lfs merge=lfs -text\n')
        self._git('add', '.gitattributes')
        self._git('commit', '-m', 'Remove source rule')
        source.rename(self.repository / 'renamed.txt')

        with self._lfs_environment():
            stage_paths(['source.txt', 'renamed.txt'], cwd=self.repository)

        self.assertEqual(self._git('show', ':renamed.txt').stdout, b'small LFS text\n')
        self.assertNotIn('renamed.txt filter=lfs', (self.repository / '.gitattributes').read_text())

    def test_explicit_lfs_override_never_places_attribute_control_files_in_lfs(self):
        """Reject a Niyān-authored rule that would filter its own control file."""

        with self._lfs_environment(), self.assertRaisesRegex(GitError, 'must remain an ordinary Git blob'):
            stage_paths(['.gitattributes'], force_lfs=True, cwd=self.repository)

    def test_git_override_treats_attribute_metacharacters_as_literal(self):
        """Persist a rule for the exact unusual filename rather than a glob."""

        unusual = 'literal [x]*?.bin'
        neighbor = 'literal x-other.bin'
        (self.repository / unusual).write_bytes(b'binary\0content')
        (self.repository / neighbor).write_bytes(b'binary\0content')

        stage_paths([unusual], force_git=True, cwd=self.repository)

        self.assertEqual(self._git('check-attr', 'filter', '--', unusual).stdout.strip(), f'{unusual}: filter: unset'.encode())
        self.assertEqual(self._git('check-attr', 'filter', '--', neighbor).stdout.strip(), f'{neighbor}: filter: lfs'.encode())

    def test_add_respects_ignores_and_stages_symlinks_without_following_them(self):
        """Delegate ignore handling to Git and keep symlink targets out of the dataset."""

        (self.repository / '.gitignore').write_text('ignored.dat\n')
        (self.repository / 'ignored.dat').write_bytes(b'ignored\0content')
        (self.root / 'outside.bin').write_bytes(b'outside\0content')
        (self.repository / 'link.bin').symlink_to(self.root / 'outside.bin')

        stage_paths(['.'], cwd=self.repository)

        staged = self._git('diff', '--cached', '--name-only').stdout.splitlines()
        self.assertIn(b'.gitignore', staged)
        self.assertIn(b'link.bin', staged)
        self.assertNotIn(b'ignored.dat', staged)
        self.assertEqual(self._git('ls-files', '--stage', 'link.bin').stdout.split()[0], b'120000')

    def test_add_delegates_nested_repositories_and_special_entries_to_git(self):
        """Preserve Git's standard handling of unsupported working-tree entries."""

        nested = self.repository / 'nested'
        subprocess.run(['git', 'init', str(nested)], check=True, capture_output=True)
        with self.assertRaisesRegex(GitError, 'could not stage'):
            stage_paths(['nested'], cwd=self.repository)

        fifo = self.repository / 'pipe'
        os.mkfifo(fifo)
        stage_paths(['pipe'], cwd=self.repository)
        self.assertNotIn(b'pipe', self._git('diff', '--cached', '--name-only').stdout.splitlines())

    def test_explicit_lfs_override_requires_git_lfs(self):
        """Allow ordinary staging without Git LFS and validate an explicit override."""

        (self.repository / 'ordinary.txt').write_text('text\n')
        (self.repository / 'needs-lfs.dat').write_bytes(b'binary\0content')

        with patch('niyan.working_copy.shutil.which', side_effect=lambda executable: '/usr/bin/git' if executable == 'git' else None):
            stage_paths(['ordinary.txt'], cwd=self.repository)
            with self.assertRaisesRegex(GitError, 'Git LFS is required'):
                stage_paths(['needs-lfs.dat'], force_lfs=True, cwd=self.repository)

        self.assertIn(b'ordinary.txt', self._git('diff', '--cached', '--name-only').stdout.splitlines())
        self.assertNotIn(b'needs-lfs.dat', self._git('diff', '--cached', '--name-only').stdout.splitlines())

    def _lfs_environment(self):
        """Temporarily expose the deterministic test Git LFS executable."""

        return patch.dict(os.environ, {'PATH': f'{self.fake_bin}{os.pathsep}{os.environ.get("PATH", "")}'})

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
