import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from niyan.cli import build_parser
from niyan.errors import GitError
from niyan.project_dependencies import _identity_from_url, _list_dependencies, add_dataset_dependency, remove_dataset_dependency, update_dataset_dependencies


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

    def test_update_uses_configured_branch_and_stages_exact_commit(self):
        """A dependency without an explicit revision follows its standard branch field."""

        self._add()
        output = io.StringIO()
        with patch('niyan.project_dependencies.select_credential', return_value=SimpleNamespace(token='secret')), patch('niyan.project_dependencies._checkout_exact_commit') as checkout, patch('niyan.project_dependencies._materialize_dependency'):
            updated = update_dataset_dependencies(dependency_path='datasets/images', paths=object(), stores=object(), cwd=self.project, api_factory=FakeDependencyApi, stdout=output)

        checkout.assert_called_once()
        self.assertEqual(updated[0]['moving_ref'], 'main')
        self.assertIn(FakeDependencyApi.commit, output.getvalue())

    def test_dependency_identity_requires_canonical_credential_free_url(self):
        """Only the documented dataset repository URL shape is accepted."""

        self.assertEqual(_identity_from_url(GIT_URL), (HOST, DATASET_ID))
        with self.assertRaises(GitError):
            _identity_from_url(f'https://token@example.test/git/{DATASET_ID}.git')

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
