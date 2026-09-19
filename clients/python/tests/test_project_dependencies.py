import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from niyan.cli import build_parser
from niyan.errors import GitError
from niyan.project_dependencies import _canonical_git_url, _checkout_exact_commit, _classify_moving_ref, _dependency_path, _ensure_dependency_checkout, _git, _identity_from_url, _indexed_gitlink, _list_dependencies, _submodule_state, _unavailable_lfs_count, add_dataset_dependency, remove_dataset_dependency, show_dataset_dependencies, update_dataset_dependencies


DATASET_ID = '22222222-2222-2222-2222-222222222222'
HOST = 'https://niyan.example'
GIT_URL = f'{HOST}/git/{DATASET_ID}.git'


class FakeDependencyApi:
    """Expose the repository identity and revision calls used by dependencies."""

    commit = None

    def __init__(self, host, token=None):
        """Remember authentication supplied by the dependency command."""

        self.host = host
        self.token = token

    def resolve_dataset(self, dataset_path):
        """Resolve the stable test dataset."""

        return 200, {'id': DATASET_ID, 'path': 'lab/images', 'git_url': GIT_URL}

    def get_dataset(self, dataset_id):
        """Return the configured default branch."""

        return 200, {'default_branch': 'main'}

    def resolve_revision(self, dataset_id, *, revision):
        """Resolve every accepted test revision to the source commit."""

        return 200, {'resolved_commit': type(self).commit}

    def list_repository_refs(self, dataset_id, *, kind='branches', limit=100, offset=0):
        """Expose one branch and one immutable tag."""

        names = ['main', 'experiment'] if kind == 'branches' else ['v1']
        return 200, {'items': [{'name': name} for name in names], 'next_offset': None}


class ProjectDependencyTests(unittest.TestCase):
    """Verify Niyān dependencies remain interoperable standard submodules."""

    def setUp(self):
        """Create isolated parent and dataset repositories."""

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.project = self.root / 'project'
        self.source = self.root / 'source'
        self._initialize_repository(self.project, 'project.txt')
        self._initialize_repository(self.source, 'data.txt')
        FakeDependencyApi.commit = self._git_text(self.source, 'rev-parse', 'HEAD')

    def tearDown(self):
        """Remove isolated repositories."""

        self.temporary_directory.cleanup()

    def test_add_records_only_standard_submodule_fields_and_exact_gitlink(self):
        """A moving dependency uses a credential-free URL and exact parent gitlink."""

        result = self._add()

        dependencies = _list_dependencies(self.project)
        self.assertEqual(dependencies, [{'name': 'datasets/images', 'path': 'datasets/images', 'url': GIT_URL, 'branch': 'main'}])
        self.assertEqual(self._git_text(self.project, 'ls-files', '--stage', '--', 'datasets/images').split()[0:2], ['160000', FakeDependencyApi.commit])
        modules = (self.project / '.gitmodules').read_text()
        self.assertNotIn('token', modules.lower())
        self.assertNotIn('\tniyan.', modules.lower())
        self.assertEqual(result['resolved_commit'], FakeDependencyApi.commit)

    def test_explicit_tag_creates_an_immutable_dependency(self):
        """Tag pins omit Git's standard moving branch field."""

        self._add(revision='v1')

        self.assertIsNone(_list_dependencies(self.project)[0]['branch'])

    def test_remove_stages_standard_submodule_deletion(self):
        """Removing a dependency stages both the gitlink and metadata changes."""

        self._add()
        subprocess.run(['git', '-C', str(self.project), 'commit', '--quiet', '-m', 'add dependency'], check=True)
        remove_dataset_dependency(dependency_path='datasets/images', cwd=self.project)

        self.assertFalse((self.project / 'datasets/images').exists())
        self.assertEqual(_list_dependencies(self.project), [])
        status = self._git_text(self.project, 'status', '--porcelain')
        self.assertIn('D  datasets/images', status)

    def test_add_rejects_existing_destination_and_cleans_failed_clone(self):
        """Protect existing paths and remove a new checkout when pinning fails."""

        existing = self.project / 'existing'
        existing.mkdir()
        with self.assertRaisesRegex(GitError, 'already exists'):
            add_dataset_dependency(host=HOST, dataset_path='lab/images', destination='existing', paths=object(), stores=object(), cwd=self.project, api_factory=FakeDependencyApi)

        def clone_function(**arguments):
            """Create the checkout directory as the real clone operation would."""

            checkout = Path(arguments['cwd']) / arguments['destination']
            checkout.mkdir(parents=True)

        with patch('niyan.project_dependencies.select_credential', return_value=SimpleNamespace(token='secret')), patch('niyan.project_dependencies._checkout_exact_commit', side_effect=GitError('pin failed')):
            with self.assertRaisesRegex(GitError, 'pin failed'):
                add_dataset_dependency(host=HOST, dataset_path='lab/images', destination='failed', paths=object(), stores=object(), cwd=self.project, api_factory=FakeDependencyApi, clone_function=clone_function)
        self.assertFalse((self.project / 'failed').exists())

    def test_update_uses_configured_branch_and_stages_exact_commit(self):
        """A dependency without an explicit revision follows its standard branch field."""

        self._add()
        output = io.StringIO()
        with patch('niyan.project_dependencies.select_credential', return_value=SimpleNamespace(token='secret')), patch('niyan.project_dependencies._checkout_exact_commit') as checkout, patch('niyan.project_dependencies._materialize_dependency'):
            updated = update_dataset_dependencies(dependency_path='datasets/images', paths=object(), stores=object(), cwd=self.project, api_factory=FakeDependencyApi, stdout=output)

        checkout.assert_called_once()
        self.assertEqual(updated[0]['moving_ref'], 'main')
        self.assertIn(FakeDependencyApi.commit, output.getvalue())

    def test_update_skips_immutable_dependencies_and_rejects_unknown_path(self):
        """Leave tag-pinned dependencies alone and diagnose misspelled paths."""

        self._add(revision='v1')

        self.assertEqual(update_dataset_dependencies(paths=object(), stores=object(), cwd=self.project, api_factory=FakeDependencyApi), [])
        with self.assertRaisesRegex(GitError, 'not a dataset dependency'):
            update_dataset_dependencies(dependency_path='datasets/missing', paths=object(), stores=object(), cwd=self.project, api_factory=FakeDependencyApi)

    def test_status_reports_standard_submodule_identity_and_state(self):
        """Show the parent pin, moving branch, checkout state, and LFS count."""

        self._add()
        output = io.StringIO()
        identity = SimpleNamespace(dataset_path='lab/images')
        with patch('niyan.project_dependencies.load_checkout_identity', side_effect=(None, identity)), patch('niyan.project_dependencies._submodule_state', return_value='clean'), patch('niyan.project_dependencies._unavailable_lfs_count', return_value=2):
            rows = show_dataset_dependencies(cwd=self.project, stdout=output)

        self.assertEqual(rows[0]['dataset_path'], 'lab/images')
        self.assertEqual(rows[0]['pinned_commit'], FakeDependencyApi.commit)
        self.assertEqual(rows[0]['moving_ref'], 'main')
        self.assertEqual(rows[0]['lfs_unavailable'], 2)
        self.assertIn('datasets/images\tlab/images', output.getvalue())

    def test_dependency_identity_requires_canonical_credential_free_url(self):
        """Only the documented dataset repository URL shape is accepted."""

        self.assertEqual(_identity_from_url(GIT_URL), (HOST, DATASET_ID))
        with self.assertRaises(GitError):
            _identity_from_url(f'https://token@example.test/git/{DATASET_ID}.git')

    def test_dependency_path_and_repository_url_validation(self):
        """Reject traversal, invalid identities, and unsafe repository metadata."""

        self.assertEqual(_dependency_path('datasets/images'), 'datasets/images')
        for value in ('', '.', '..', '../images', '/datasets/images', 'datasets/../images'):
            with self.subTest(path=value):
                with self.assertRaises(GitError):
                    _dependency_path(value)

        self.assertEqual(_canonical_git_url(host=HOST, dataset_id=DATASET_ID, value=GIT_URL), GIT_URL)
        invalid = [
            (DATASET_ID, None),
            ('not-a-uuid', GIT_URL),
            (DATASET_ID, f'https://token@niyan.example/git/{DATASET_ID}.git'),
            (DATASET_ID, f'https://other.example/git/{DATASET_ID}.git'),
            (DATASET_ID, f'{GIT_URL}?token=secret'),
        ]
        for dataset_id, value in invalid:
            with self.subTest(dataset_id=dataset_id, value=value):
                with self.assertRaises(GitError):
                    _canonical_git_url(host=HOST, dataset_id=dataset_id, value=value)

        with self.assertRaisesRegex(GitError, 'invalid Niyān dataset identity'):
            _identity_from_url(f'{HOST}/git/{"0" * 36}.git')

    def test_moving_ref_classification_handles_pagination_and_ambiguity(self):
        """Follow paginated refs and refuse a name shared by branch and tag."""

        class PaginatedApi:
            """Return a branch on the second page and no matching tag."""

            def list_repository_refs(self, dataset_id, *, kind, limit, offset):
                """Return deterministic paginated ref data."""

                if kind == 'branches' and offset == 0:
                    return 200, {'items': [], 'next_offset': 100}
                names = ['experiment'] if kind == 'branches' else ['v1']
                return 200, {'items': [{'name': name} for name in names], 'next_offset': None}

        self.assertEqual(_classify_moving_ref(api=PaginatedApi(), dataset_id=DATASET_ID, revision='experiment'), 'experiment')
        self.assertIsNone(_classify_moving_ref(api=PaginatedApi(), dataset_id=DATASET_ID, revision='missing'))

        api = FakeDependencyApi(HOST)
        original = api.list_repository_refs
        api.list_repository_refs = lambda dataset_id, kind, limit, offset: (200, {'items': [{'name': 'shared'}], 'next_offset': None})
        with self.assertRaisesRegex(GitError, 'ambiguous'):
            _classify_moving_ref(api=api, dataset_id=DATASET_ID, revision='shared')
        api.list_repository_refs = original

    def test_checkout_and_initialization_failures_are_translated(self):
        """Reject unsuccessful authenticated fetch, checkout, and submodule init."""

        credential = SimpleNamespace(token='secret')
        context = unittest.mock.MagicMock()
        context.__enter__.return_value = ['git']
        with patch('niyan.project_dependencies._authenticated_git', return_value=context), patch('niyan.project_dependencies._git_environment', return_value={}), patch('niyan.project_dependencies._run_git', return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(GitError, 'fetch the pinned'):
                _checkout_exact_commit(checkout=self.project, host=HOST, credential=credential, commit=FakeDependencyApi.commit, environment={})

        with patch('niyan.project_dependencies._authenticated_git', return_value=context), patch('niyan.project_dependencies._git_environment', return_value={}), patch('niyan.project_dependencies._run_git', side_effect=(SimpleNamespace(returncode=0), SimpleNamespace(returncode=1))):
            with self.assertRaisesRegex(GitError, 'check out the pinned'):
                _checkout_exact_commit(checkout=self.project, host=HOST, credential=credential, commit=FakeDependencyApi.commit, environment={})

        checkout = self.project / 'uninitialized'
        with patch('niyan.project_dependencies._authenticated_git', return_value=context), patch('niyan.project_dependencies._git_environment', return_value={}), patch('niyan.project_dependencies._run_git', return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(GitError, 'initialize the dataset'):
                _ensure_dependency_checkout(project=self.project, checkout=checkout, dependency_path='uninitialized', host=HOST, credential=credential, environment={})

    def test_dependency_state_helpers_reject_invalid_git_data(self):
        """Map Git markers precisely and fail closed around malformed gitlinks."""

        with patch('niyan.project_dependencies._git_text', return_value='100644 invalid 0\tdata'):
            with self.assertRaisesRegex(GitError, 'not recorded as a Git submodule'):
                _indexed_gitlink(self.project, 'data')

        for marker, expected in (('', 'uninitialized'), ('-abc data', 'uninitialized'), ('+abc data', 'out-of-sync'), ('Uabc data', 'conflict'), (' abc data', 'clean')):
            with self.subTest(marker=marker):
                with patch('niyan.project_dependencies._git', return_value=SimpleNamespace(stdout=marker.encode())):
                    self.assertEqual(_submodule_state(self.project, 'data'), expected)

        with patch('niyan.project_dependencies._git_text', side_effect=GitError('unavailable')):
            self.assertEqual(_unavailable_lfs_count(self.project), '-')

    def test_git_runner_reports_missing_process_and_nonzero_exit(self):
        """Translate dependency, process, and command failures consistently."""

        with patch('niyan.project_dependencies.shutil.which', return_value=None):
            with self.assertRaisesRegex(GitError, 'Git is required'):
                _git(self.project, ['status'], operation='inspect the project')
        with patch('niyan.project_dependencies.shutil.which', return_value='/usr/bin/git'), patch('niyan.project_dependencies.subprocess.run', side_effect=OSError('process failed')):
            with self.assertRaisesRegex(GitError, 'could not inspect'):
                _git(self.project, ['status'], operation='inspect the project')
        with patch('niyan.project_dependencies.shutil.which', return_value='/usr/bin/git'), patch('niyan.project_dependencies.subprocess.run', return_value=SimpleNamespace(returncode=2)):
            with self.assertRaisesRegex(GitError, 'could not inspect'):
                _git(self.project, ['status'], operation='inspect the project')

    def test_cli_exposes_the_documented_dependency_commands(self):
        """The public grammar matches the project-dependency specification."""

        add = build_parser().parse_args(['dataset', 'add', 'lab/images', 'datasets/images', '--ref', 'v1', '--host', HOST])
        remove = build_parser().parse_args(['dataset', 'remove', 'datasets/images'])
        update = build_parser().parse_args(['dataset', 'update', 'datasets/images', '--ref', 'experiment'])
        status = build_parser().parse_args(['dataset', 'status'])

        self.assertEqual((add.dataset_command, add.dataset_path, add.path, add.revision), ('add', 'lab/images', 'datasets/images', 'v1'))
        self.assertEqual((remove.dataset_command, remove.path), ('remove', 'datasets/images'))
        self.assertEqual((update.dataset_command, update.path, update.revision), ('update', 'datasets/images', 'experiment'))
        self.assertEqual(status.dataset_command, 'status')

    def _add(self, *, revision=None):
        """Add the fixture source using real Git recording and mocked network work."""

        def clone_function(**arguments):
            destination = Path(arguments['cwd']) / arguments['destination']
            subprocess.run(['git', 'clone', '--quiet', '--', str(self.source), str(destination)], check=True)
            return destination

        with patch('niyan.project_dependencies.select_credential', return_value=SimpleNamespace(token='secret')), patch('niyan.project_dependencies._checkout_exact_commit'), patch('niyan.project_dependencies._materialize_dependency'):
            return add_dataset_dependency(host=HOST, dataset_path='lab/images', destination='datasets/images', revision=revision, paths=object(), stores=object(), cwd=self.project, api_factory=FakeDependencyApi, clone_function=clone_function, stdout=io.StringIO())

    def _initialize_repository(self, path, filename):
        """Create one repository with a single commit."""

        path.mkdir()
        subprocess.run(['git', 'init', '--quiet', '--initial-branch=main', str(path)], check=True)
        subprocess.run(['git', '-C', str(path), 'config', 'user.name', 'Niyan Tests'], check=True)
        subprocess.run(['git', '-C', str(path), 'config', 'user.email', 'tests@niyan.invalid'], check=True)
        (path / filename).write_text('fixture\n')
        subprocess.run(['git', '-C', str(path), 'add', filename], check=True)
        subprocess.run(['git', '-C', str(path), 'commit', '--quiet', '-m', 'fixture'], check=True)

    def _git_text(self, cwd, *arguments):
        """Run Git and decode one successful test result."""

        return subprocess.run(['git', '-C', str(cwd), *arguments], check=True, capture_output=True, text=True).stdout.strip()


if __name__ == '__main__':
    unittest.main()
