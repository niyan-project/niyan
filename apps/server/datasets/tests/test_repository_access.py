import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import Client, LiveServerTestCase, TestCase, override_settings

from accounts.models import AccessToken, User
from accounts.tokens import create_access_token
from datasets.models import Dataset, DatasetGrant
from datasets.services import create_dataset
from namespaces.models import NamespaceMembership


class RepositoryFixtureMixin:
    """Create a real bare repository with representative Git content."""

    def create_repository_fixture(self):
        """Create users, a dataset, commits, a tag, and an LFS pointer."""

        self.repository_directory = TemporaryDirectory()
        self.working_directory = TemporaryDirectory()
        self.repository_root = Path(self.repository_directory.name)
        self.settings_override = override_settings(REPOSITORIES_ROOT=self.repository_root)
        self.settings_override.enable()
        self.user = User.objects.create_user(username='researcher')
        self.dataset = create_dataset(namespace=self.user.personal_namespace, slug='images', name='Images', created_by=self.user)

        working_path = Path(self.working_directory.name)
        self.run_git('init', '--initial-branch=main', str(working_path))
        self.run_git('-C', str(working_path), 'config', 'user.name', 'Researcher')
        self.run_git('-C', str(working_path), 'config', 'user.email', 'researcher@example.test')
        (working_path / 'README.md').write_text('# Images\n\nDataset documentation.\n')
        (working_path / 'notes.txt').write_text('first version\n')
        (working_path / 'nested').mkdir()
        (working_path / 'nested' / 'sample.csv').write_text('value\n42\n')
        self.run_git('-C', str(working_path), 'add', '.')
        self.run_git('-C', str(working_path), 'commit', '-m', 'Initial dataset')
        self.run_git('-C', str(working_path), 'tag', 'v1')

        lfs_object_id = 'a' * 64
        (working_path / 'large.bin').write_text(f'version https://git-lfs.github.com/spec/v1\noid sha256:{lfs_object_id}\nsize 123456\n')
        (working_path / 'notes.txt').write_text('second version\n')
        self.run_git('-C', str(working_path), 'add', '.')
        self.run_git('-C', str(working_path), 'commit', '-m', 'Add large data pointer')
        self.run_git('-C', str(working_path), 'remote', 'add', 'origin', str(self.repository_root / f'{self.dataset.id}.git'))
        self.run_git('-C', str(working_path), 'push', '--tags', 'origin', 'main')
        self.main_commit = self.run_git('-C', str(working_path), 'rev-parse', 'HEAD').stdout.strip()
        self.lfs_object_id = lfs_object_id

    def destroy_repository_fixture(self):
        """Restore repository settings and remove temporary Git data."""

        self.settings_override.disable()
        self.working_directory.cleanup()
        self.repository_directory.cleanup()

    def run_git(self, *arguments, check=True, env=None):
        """Run Git for test setup without invoking a shell.

        Parameters
        ----------
        *arguments : str
            Git arguments.
        check : bool, optional
            Raise when Git exits unsuccessfully.
        env : dict, optional
            Explicit child environment.

        Returns
        -------
        subprocess.CompletedProcess
            Captured Git process result.
        """

        return subprocess.run(['git', *arguments], check=check, capture_output=True, text=True, env=env)


class RepositoryBrowsingApiTests(RepositoryFixtureMixin, TestCase):
    """Verify refs, history, trees, blobs, and README API behavior."""

    def setUp(self):
        """Create a repository fixture and authenticated browser client."""

        self.create_repository_fixture()
        self.client = Client()
        self.client.force_login(self.user)

    def tearDown(self):
        """Remove the repository fixture."""

        self.destroy_repository_fixture()

    def repository_url(self, suffix):
        """Return one browsing endpoint for the fixture dataset."""

        return f'/api/v1/datasets/{self.dataset.id}/repository/{suffix}'

    def test_browse_refs_commits_tree_and_readme(self):
        """Read standard Git concepts without persisting a duplicate index."""

        branches = self.client.get(self.repository_url('refs'), {'kind': 'branches'}).json()
        tags = self.client.get(self.repository_url('refs'), {'kind': 'tags'}).json()
        commits = self.client.get(self.repository_url('commits')).json()
        tree = self.client.get(self.repository_url('tree')).json()
        nested_tree = self.client.get(self.repository_url('tree'), {'path': 'nested'}).json()
        readme = self.client.get(self.repository_url('readme')).json()

        self.assertEqual([item['name'] for item in branches['items']], ['main'])
        self.assertEqual([item['name'] for item in tags['items']], ['v1'])
        self.assertEqual(len(commits['items']), 2)
        self.assertEqual(commits['resolved_commit'], self.main_commit)
        self.assertEqual(commits['items'][0]['subject'], 'Add large data pointer')
        self.assertIn('README.md', [item['name'] for item in tree['items']])
        self.assertEqual([item['path'] for item in nested_tree['items']], ['nested/sample.csv'])
        self.assertEqual(readme['resolved_commit'], self.main_commit)
        self.assertEqual(readme['path'], 'README.md')
        self.assertIn('Dataset documentation.', readme['content'])

    def test_blob_metadata_and_raw_download_distinguish_lfs_pointer(self):
        """Stream ordinary blobs while refusing to misrepresent LFS pointers."""

        metadata_response = self.client.get(self.repository_url('blob'), {'path': 'notes.txt'})
        raw_response = self.client.get(self.repository_url('blob/raw'), {'path': 'notes.txt'})
        lfs_metadata_response = self.client.get(self.repository_url('blob'), {'path': 'large.bin'})
        lfs_raw_response = self.client.get(self.repository_url('blob/raw'), {'path': 'large.bin'})

        self.assertEqual(metadata_response.status_code, 200)
        self.assertFalse(metadata_response.json()['is_lfs'])
        self.assertEqual(raw_response.status_code, 200)
        self.assertEqual(b''.join(raw_response.streaming_content), b'second version\n')
        self.assertEqual(raw_response['X-Niyan-Resolved-Commit'], self.main_commit)
        self.assertTrue(lfs_metadata_response.json()['is_lfs'])
        self.assertEqual(lfs_metadata_response.json()['lfs_object_id'], self.lfs_object_id)
        self.assertEqual(lfs_metadata_response.json()['lfs_size'], 123456)
        self.assertEqual(lfs_raw_response.status_code, 409)
        self.assertEqual(lfs_raw_response.json()['code'], 'lfs_content_unavailable')

    def test_repository_browsing_requires_repository_scope_for_bearer_token(self):
        """Keep API metadata scopes separate from repository-content scopes."""

        _, read_api_token = create_access_token(user=self.user, name='Metadata only', scopes=['read_api'], origin=AccessToken.Origin.MANUAL)
        _, repository_token = create_access_token(user=self.user, name='Repository reader', scopes=['read_repository'], origin=AccessToken.Origin.MANUAL)
        self.client.logout()

        denied_response = self.client.get(self.repository_url('refs'), HTTP_AUTHORIZATION=f'Bearer {read_api_token}')
        allowed_response = self.client.get(self.repository_url('refs'), HTTP_AUTHORIZATION=f'Bearer {repository_token}')

        self.assertEqual(denied_response.status_code, 403)
        self.assertEqual(allowed_response.status_code, 200)

    def test_repository_browsing_enforces_token_dataset_boundary(self):
        """Reject a valid repository token bound to another dataset."""

        other_dataset = create_dataset(namespace=self.user.personal_namespace, slug='other', name='Other', created_by=self.user)
        _, bounded_token = create_access_token(
            user=self.user,
            name='Other dataset only',
            scopes=['read_repository'],
            origin=AccessToken.Origin.MANUAL,
            dataset=other_dataset,
        )
        self.client.logout()

        response = self.client.get(self.repository_url('refs'), HTTP_AUTHORIZATION=f'Bearer {bounded_token}')

        self.assertEqual(response.status_code, 403)

    def test_dataset_grant_controls_repository_browsing(self):
        """Use the shared dataset policy rather than personal ownership checks."""

        collaborator = User.objects.create_user(username='collaborator')
        self.client.force_login(collaborator)
        denied_response = self.client.get(self.repository_url('refs'))
        DatasetGrant.objects.create(dataset=self.dataset, user=collaborator, role=NamespaceMembership.Role.READER)
        allowed_response = self.client.get(self.repository_url('refs'))

        self.assertEqual(denied_response.status_code, 404)
        self.assertEqual(allowed_response.status_code, 200)

    def test_repository_errors_are_sanitized(self):
        """Return stable failures for missing revisions, paths, and invalid input."""

        missing_revision = self.client.get(self.repository_url('commits'), {'revision': 'missing'})
        missing_path = self.client.get(self.repository_url('tree'), {'path': 'missing'})
        invalid_path = self.client.get(self.repository_url('tree'), {'path': '../outside'})

        self.assertEqual(missing_revision.status_code, 404)
        self.assertEqual(missing_revision.json()['code'], 'revision_not_found')
        self.assertEqual(missing_path.status_code, 404)
        self.assertEqual(missing_path.json()['code'], 'path_not_found')
        self.assertEqual(invalid_path.status_code, 422)

    def test_repository_pagination_rejects_excessive_offset(self):
        """Bound work derived from caller-controlled listing offsets."""

        response = self.client.get(self.repository_url('refs'), {'offset': 10001})

        self.assertEqual(response.status_code, 422)


class GitSmartHttpTests(RepositoryFixtureMixin, LiveServerTestCase):
    """Verify real Git clone behavior through the Django CGI adapter."""

    def setUp(self):
        """Create a repository and a read-repository access token."""

        self.create_repository_fixture()
        self.access_token, self.raw_token = create_access_token(user=self.user, name='Clone token', scopes=['read_repository'], origin=AccessToken.Origin.CLI)

    def tearDown(self):
        """Remove repository and clone fixtures."""

        self.destroy_repository_fixture()

    def test_git_clone_uses_basic_token_authentication(self):
        """Clone through Git's protocol without exposing the token in the URL."""

        clone_directory = TemporaryDirectory()
        askpass_directory = TemporaryDirectory()
        try:
            askpass = Path(askpass_directory.name) / 'askpass.sh'
            askpass.write_text('#!/bin/sh\ncase "$1" in\n  *Username*) printf "%s\\n" "$NIYAN_TEST_USERNAME" ;;\n  *) printf "%s\\n" "$NIYAN_TEST_PASSWORD" ;;\nesac\n')
            askpass.chmod(0o700)
            destination = Path(clone_directory.name) / 'clone'
            environment = os.environ.copy()
            environment.update(
                {
                    'GIT_ASKPASS': str(askpass),
                    'GIT_TERMINAL_PROMPT': '0',
                    'NIYAN_TEST_USERNAME': self.user.username,
                    'NIYAN_TEST_PASSWORD': self.raw_token,
                }
            )
            result = self.run_git(
                '-c',
                'credential.helper=',
                'clone',
                f'{self.live_server_url.replace("http://", f"http://{self.user.username}@")}/git/{self.dataset.id}.git',
                str(destination),
                check=False,
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((destination / 'notes.txt').read_text(), 'second version\n')
            self.assertEqual((destination / 'README.md').read_text(), '# Images\n\nDataset documentation.\n')
        finally:
            askpass_directory.cleanup()
            clone_directory.cleanup()

    def test_git_http_rejects_missing_scope_and_receive_pack(self):
        """Keep read transport scope-limited and reject write negotiation."""

        _, wrong_scope_token = create_access_token(user=self.user, name='API token', scopes=['read_api'], origin=AccessToken.Origin.MANUAL)
        client = Client()
        wrong_scope_header = _basic_header(self.user.username, wrong_scope_token)
        valid_header = _basic_header(self.user.username, self.raw_token)

        denied_response = client.get(
            f'/git/{self.dataset.id}.git/info/refs',
            {'service': 'git-upload-pack'},
            HTTP_AUTHORIZATION=wrong_scope_header,
        )
        write_response = client.get(
            f'/git/{self.dataset.id}.git/info/refs',
            {'service': 'git-receive-pack'},
            HTTP_AUTHORIZATION=valid_header,
        )

        self.assertEqual(denied_response.status_code, 403)
        self.assertEqual(write_response.status_code, 405)

    def test_git_http_challenges_missing_credentials(self):
        """Return the standard Basic challenge required by Git clients."""

        response = Client().get(f'/git/{self.dataset.id}.git/info/refs', {'service': 'git-upload-pack'})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response['WWW-Authenticate'], 'Basic realm="Niyan Git"')

    def test_niyan_cli_resolves_and_clones_without_persisting_token_in_git(self):
        """Protect the complete CLI-to-REST-to-smart-HTTP clone workflow."""

        cli_state = TemporaryDirectory()
        checkout_root = TemporaryDirectory()
        try:
            state_root = Path(cli_state.name)
            config_home = state_root / 'config' / 'niyan'
            data_home = state_root / 'data' / 'niyan'
            config_home.mkdir(parents=True)
            data_home.mkdir(parents=True)
            host = self.live_server_url
            binding = {
                'token_id': str(self.access_token.id),
                'username': self.user.username,
                'storage': 'file',
            }
            (config_home / 'config.json').write_text(json.dumps({'version': 1, 'default_host': host, 'hosts': {host: {'default': binding, 'datasets': {}}}}))
            credentials_path = data_home / 'credentials.json'
            credentials_path.write_text(json.dumps({'version': 1, 'tokens': {f'{host}\n{self.access_token.id}': self.raw_token}}))
            credentials_path.chmod(0o600)

            destination = Path(checkout_root.name) / 'images'
            repository_root = Path(__file__).resolve().parents[4]
            environment = os.environ.copy()
            environment.update(
                {
                    'PYTHONPATH': str(repository_root / 'clients' / 'python' / 'src'),
                    'XDG_CONFIG_HOME': str(state_root / 'config'),
                    'XDG_DATA_HOME': str(state_root / 'data'),
                    'NIYAN_HOST': host,
                }
            )
            result = subprocess.run(
                [sys.executable, '-m', 'niyan', 'dataset', 'clone', 'researcher/images', str(destination)],
                cwd=checkout_root.name,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((destination / 'notes.txt').read_text(), 'second version\n')
            git_config = (destination / '.git' / 'config').read_text()
            self.assertNotIn(self.raw_token, git_config)
            self.assertNotIn(self.raw_token, result.stdout)
            self.assertNotIn(self.raw_token, result.stderr)
        finally:
            checkout_root.cleanup()
            cli_state.cleanup()


def _basic_header(username, token):
    """Return one HTTP Basic header for Git endpoint tests."""

    encoded = base64.b64encode(f'{username}:{token}'.encode()).decode()
    return f'Basic {encoded}'
