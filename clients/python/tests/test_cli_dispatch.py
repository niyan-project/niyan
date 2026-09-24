import io
import subprocess
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from niyan.cli import _error_exit_status, _read_access_token, main
from niyan.errors import ApiError, ConfigurationError, CredentialError, GitConflictError, GitDependencyError, NiyanCliError


class CliDispatchTests(unittest.TestCase):
    """Verify every public command reaches its intended workflow boundary."""

    def setUp(self):
        """Replace environment-dependent construction with stable sentinels."""

        self.paths = Mock(name='paths')
        self.stores = Mock(name='stores')
        self.stack = ExitStack()
        self.stack.enter_context(patch('niyan.cli.AppPaths.from_environment', return_value=self.paths))
        self.stack.enter_context(patch('niyan.cli.CredentialStores', return_value=self.stores))
        self.stack.enter_context(patch('niyan.cli.resolve_host', return_value='https://data.example.test'))

    def tearDown(self):
        """Restore all command dependencies after each test."""

        self.stack.close()

    def invoke(self, arguments, target):
        """Run one command and return its patched workflow function."""

        with patch(f'niyan.cli.{target}') as callback:
            self.assertEqual(main(arguments), 0)
        callback.assert_called_once()
        return callback.call_args

    def test_internal_transfer_and_credential_helper_dispatch(self):
        """Keep private Git LFS and credential protocols outside public parsing."""

        with patch('niyan.cli.run_lfs_transfer', return_value=17) as transfer:
            self.assertEqual(main(['_lfs-transfer']), 17)
        transfer.assert_called_once_with(paths=self.paths, stores=self.stores, cwd=Path.cwd())

        arguments = ['_credential-helper', 'get', '--host', 'https://data.example.test', '--username', 'researcher', '--token-file', '/tmp/token']
        with patch('niyan.cli.credential_helper', return_value=0) as helper:
            self.assertEqual(main(arguments), 0)
        self.assertEqual(helper.call_args.kwargs['token_file'], '/tmp/token')
        self.assertEqual(main(['_credential-helper', 'store', '--host', 'https://data.example.test', '--username', 'researcher']), 0)

    def test_browser_login_dispatches_requested_scope_and_storage(self):
        """Forward device-login options without changing their semantics."""

        call = self.invoke(['auth', 'login', 'data.example.test', '--local', '--dataset', 'lab/images', '--read-only', '--insecure-storage', '--no-browser'], 'login')

        self.assertEqual(call.kwargs['host'], 'https://data.example.test')
        self.assertTrue(call.kwargs['local'])
        self.assertEqual(call.kwargs['dataset_path'], 'lab/images')
        self.assertTrue(call.kwargs['read_only'])
        self.assertTrue(call.kwargs['insecure_storage'])
        self.assertTrue(call.kwargs['no_browser'])

    def test_dataset_forge_commands_dispatch_complete_options(self):
        """Connect clone and remote dataset CRUD grammar to service functions."""

        cases = [
            (['dataset', 'clone', 'lab/images', 'inputs', '--full-history', '--include', 'raw/**', '--exclude', 'tmp/**', '--metadata-only'], 'clone_dataset', {'dataset_path': 'lab/images', 'destination': 'inputs', 'full_history': True, 'include': ['raw/**'], 'exclude': ['tmp/**'], 'metadata_only': True}),
            (['dataset', 'create', 'lab/images', '--name', 'Images', '--clone', '--full-history'], 'create_remote_dataset', {'dataset_path': 'lab/images', 'name': 'Images', 'clone': True, 'full_history': True}),
            (['dataset', 'list', 'lab', '--limit', '25'], 'list_remote_datasets', {'namespace_path': 'lab', 'limit': 25}),
            (['dataset', 'view', 'lab/images', '--web'], 'view_remote_dataset', {'dataset_path': 'lab/images', 'web': True}),
            (['dataset', 'edit', 'lab/images', '--slug', 'photos', '--name', 'Photos'], 'edit_remote_dataset', {'dataset_path': 'lab/images', 'slug': 'photos', 'name': 'Photos'}),
            (['dataset', 'delete', 'lab/images', '--confirm', 'lab/images'], 'delete_remote_dataset', {'dataset_path': 'lab/images', 'confirmation': 'lab/images'}),
        ]
        for arguments, target, expected in cases:
            with self.subTest(target=target):
                call = self.invoke(arguments, target)
                for name, value in expected.items():
                    self.assertEqual(call.kwargs[name], value)
                self.assertEqual(call.kwargs.get('host'), 'https://data.example.test')

    def test_remote_file_commands_dispatch_complete_options(self):
        """Connect remote listing, streaming, and download grammar to services."""

        cases = [
            (['dataset', 'tree', 'lab/images', 'raw', '--ref', 'release/v1'], 'show_remote_tree', {'repository_path': 'raw'}),
            (['dataset', 'cat', 'lab/images', 'raw/image.tif', '--ref', 'release/v1'], 'stream_remote_file', {'repository_path': 'raw/image.tif'}),
            (['dataset', 'download', 'lab/images', 'raw', 'labels.csv', '--ref', 'release/v1', '--output', 'downloads'], 'download_remote_paths', {'repository_paths': ['raw', 'labels.csv'], 'output_directory': 'downloads'}),
        ]
        for arguments, target, expected in cases:
            with self.subTest(target=target):
                call = self.invoke(arguments, target)
                self.assertEqual(call.kwargs['dataset_path'], 'lab/images')
                self.assertEqual(call.kwargs['revision'], 'release/v1')
                for name, value in expected.items():
                    self.assertEqual(call.kwargs[name], value)

    def test_project_dependency_commands_dispatch(self):
        """Connect submodule add, remove, update, and status grammar to services."""

        cases = [
            (['dataset', 'add', 'lab/images', 'data/images', '--ref', 'release/v1'], 'add_dataset_dependency', {'dataset_path': 'lab/images', 'destination': 'data/images', 'revision': 'release/v1'}),
            (['dataset', 'remove', 'data/images'], 'remove_dataset_dependency', {'dependency_path': 'data/images'}),
            (['dataset', 'update', 'data/images', '--ref', 'main'], 'update_dataset_dependencies', {'dependency_path': 'data/images', 'revision': 'main'}),
            (['dataset', 'status'], 'show_dataset_dependencies', {}),
        ]
        for arguments, target, expected in cases:
            with self.subTest(target=target):
                call = self.invoke(arguments, target)
                for name, value in expected.items():
                    self.assertEqual(call.kwargs[name], value)

    def test_all_access_commands_dispatch_human_principals(self):
        """Preserve list, grant, edit, and revoke access semantics."""

        cases = [
            (['dataset', 'access', 'list', 'lab/images', '--json'], 'list_dataset_access', {}),
            (['dataset', 'access', 'grant', 'lab/images', '--user', 'ada', '--role', 'reader'], 'grant_dataset_access', {'principal_type': 'user', 'principal': 'ada', 'role': 'reader'}),
            (['dataset', 'access', 'edit', 'lab/images', '--group', 'lab/vision', '--role', 'maintainer'], 'edit_dataset_access', {'principal_type': 'group', 'principal': 'lab/vision', 'role': 'maintainer'}),
            (['dataset', 'access', 'revoke', 'lab/images', '--user', 'ada'], 'revoke_dataset_access', {'principal_type': 'user', 'principal': 'ada'}),
        ]
        for arguments, target, expected in cases:
            with self.subTest(target=target):
                call = self.invoke(arguments, target)
                self.assertEqual(call.kwargs['dataset_path'], 'lab/images')
                for name, value in expected.items():
                    self.assertEqual(call.kwargs[name], value)

    def test_working_copy_commands_dispatch(self):
        """Connect ordinary repository commands to their Git-backed services."""

        cases = [
            (['status'], 'show_status', {}),
            (['add', '--all', '--verbose'], 'stage_paths', {'paths': [], 'all_paths': True, 'force_lfs': False, 'force_git': False, 'verbose': True}),
            (['add', '--lfs', 'large.bin'], 'stage_paths', {'paths': ['large.bin'], 'all_paths': False, 'force_lfs': True, 'force_git': False}),
            (['restore', '--staged', 'data.csv'], 'restore_paths', {'paths': ['data.csv'], 'staged': True}),
            (['commit', '-m', 'Add data'], 'commit_changes', {'message': 'Add data'}),
            (['diff', '--staged'], 'show_diff', {'staged': True}),
            (['log', '--limit', '12'], 'show_log', {'limit': 12}),
            (['switch', '--create', 'experiment', 'main'], 'switch_branch', {'branch': 'experiment', 'create': True, 'start_point': 'main'}),
            (['merge', 'experiment'], 'merge_revision', {'revision': 'experiment'}),
        ]
        for arguments, target, expected in cases:
            with self.subTest(target=target):
                call = self.invoke(arguments, target)
                for name, value in expected.items():
                    if name == 'paths':
                        self.assertEqual(call.args[0], value)
                    elif name in ('branch', 'revision'):
                        self.assertEqual(call.args[0], value)
                    else:
                        self.assertEqual(call.kwargs[name], value)

    def test_branch_and_tag_commands_dispatch(self):
        """Connect all branch and annotated-tag operations to their services."""

        cases = [
            (['branch', 'list'], 'list_branches', (), {}),
            (['branch', 'create', 'experiment', 'main'], 'create_branch', ('experiment',), {'start_point': 'main'}),
            (['branch', 'rename', 'old', 'new'], 'rename_branch', ('old', 'new'), {}),
            (['branch', 'delete', 'old'], 'delete_branch', ('old',), {}),
            (['tag', 'list'], 'list_tags', (), {}),
            (['tag', 'create', 'v1', '-m', 'Release'], 'create_tag', ('v1',), {'message': 'Release'}),
            (['tag', 'delete', 'v1'], 'delete_tag', ('v1',), {}),
        ]
        for arguments, target, positional, keywords in cases:
            with self.subTest(target=target):
                call = self.invoke(arguments, target)
                self.assertEqual(call.args, positional)
                for name, value in keywords.items():
                    self.assertEqual(call.kwargs[name], value)

    def test_remote_sync_and_cache_commands_dispatch(self):
        """Connect fetch, pull, push, and cache maintenance to services."""

        cases = [
            (['fetch'], 'fetch_dataset', {}),
            (['pull', '--full-history', '--include', 'raw/**', '--exclude', 'tmp/**', '--metadata-only'], 'pull_dataset', {'full_history': True, 'include': ['raw/**'], 'exclude': ['tmp/**'], 'metadata_only': True}),
            (['push'], 'push_dataset', {}),
            (['cache', 'status'], 'show_cache_status', {}),
            (['cache', 'prune', '--dry-run'], 'prune_cache', {'dry_run': True}),
        ]
        for arguments, target, expected in cases:
            with self.subTest(target=target):
                call = self.invoke(arguments, target)
                for name, value in expected.items():
                    self.assertEqual(call.kwargs[name], value)

    def test_expected_domain_errors_become_stable_exit_statuses(self):
        """Keep service failures concise and machine-classifiable."""

        cases = [
            (CredentialError('credential'), 3),
            (ApiError('input', status=422), 2),
            (ApiError('auth', status=401), 3),
            (ApiError('permission', status=403), 4),
            (ApiError('missing', status=404), 5),
            (ApiError('conflict', status=409), 6),
            (ApiError('busy', status=429), 7),
            (ApiError('network'), 7),
            (ConfigurationError('config'), 2),
            (GitConflictError('git conflict'), 6),
            (GitDependencyError('missing git-lfs'), 8),
            (NiyanCliError('other'), 1),
        ]
        for error, expected in cases:
            with self.subTest(error=type(error).__name__, status=getattr(error, 'status', None)):
                self.assertEqual(_error_exit_status(error), expected)

        with patch('niyan.cli.show_status', side_effect=ApiError('denied', status=403)), patch('niyan.cli.sys.stderr', new_callable=io.StringIO) as stderr:
            self.assertEqual(main(['status']), 4)
            self.assertEqual(stderr.getvalue(), 'error: denied\n')

    def test_manual_token_rejects_unbounded_or_faulty_input(self):
        """Bound piped secrets and tolerate streams without terminal metadata."""

        class FaultyInput(io.StringIO):
            """Raise while checking whether the stream is interactive."""

            def isatty(self):
                """Simulate a detached standard input stream."""

                raise OSError('detached')

        self.assertEqual(_read_access_token(stdin=FaultyInput('token')), 'token')
        with self.assertRaisesRegex(CredentialError, 'too long'):
            _read_access_token(stdin=io.StringIO('x' * 4097))

    def test_module_entrypoint_reports_version(self):
        """Keep ``python -m niyan`` wired to the same public CLI."""

        completed = subprocess.run([sys.executable, '-m', 'niyan', '--version'], check=True, capture_output=True, text=True)

        self.assertRegex(completed.stdout, r'^niyan 0\.6\.0\n$')


if __name__ == '__main__':
    unittest.main()
