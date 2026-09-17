import io
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from niyan.auth import login, resolve_host, select_credential
from niyan.config import AppPaths, Configuration, CredentialBinding, find_local_config
from niyan.credentials import CredentialStores, InsecureFileCredentialStore
from niyan.errors import ConfigurationError, CredentialError
from niyan.git import clone_dataset, credential_helper
from niyan.http import normalize_host


class FakeKeyring:
    """Provide an in-memory keyring-compatible test double."""

    def __init__(self):
        """Initialize empty service and username storage."""

        self.values = {}

    def set_password(self, service, username, password):
        """Persist one fake keyring password."""

        self.values[(service, username)] = password

    def get_password(self, service, username):
        """Return one fake keyring password if present."""

        return self.values.get((service, username))

    def delete_password(self, service, username):
        """Delete one fake keyring password."""

        self.values.pop((service, username), None)


class FakeDeviceApi:
    """Simulate the public device flow without importing server code."""

    polls = 0

    def __init__(self, host, token=None):
        """Remember selected host and optional exchanged credential."""

        self.host = host
        self.token = token

    def start_device_authorization(self, *, name, scopes, dataset_path=None):
        """Return deterministic device authorization metadata."""

        return 201, {
            'device_code': 'private-device-code',
            'user_code': 'ABCD-EFGH',
            'verification_uri': f'{self.host}/auth/device',
            'verification_uri_complete': f'{self.host}/auth/device?user_code=ABCD-EFGH',
            'expires_in': 600,
            'interval': 1,
        }

    def exchange_device_code(self, device_code):
        """Return pending once and then the approved token."""

        type(self).polls += 1
        if type(self).polls == 1:
            return 202, {'code': 'authorization_pending'}
        return 200, {'token': 'niyan_selector_secret'}

    def current_user(self):
        """Return token metadata required for non-secret binding."""

        return 200, {
            'username': 'researcher',
            'access_token': {
                'id': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
                'dataset_id': None,
            },
        }


class CliStateTests(unittest.TestCase):
    """Verify global, local, secure, and explicit insecure state handling."""

    def setUp(self):
        """Create isolated user paths and a Git working tree."""

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.paths = AppPaths(config_home=self.root / 'config', data_home=self.root / 'data')
        self.keyring = FakeKeyring()
        self.stores = CredentialStores(paths=self.paths, keyring_module=self.keyring)
        self.repository = self.root / 'project'
        self.repository.mkdir()
        subprocess.run(['git', 'init', '--initial-branch=main', str(self.repository)], check=True, capture_output=True)

    def tearDown(self):
        """Remove isolated CLI and Git state."""

        self.temporary_directory.cleanup()
        FakeDeviceApi.polls = 0

    def test_global_login_saves_non_secret_binding_and_keyring_secret(self):
        """Make a default login available from other working directories."""

        output = io.StringIO()
        binding = login(
            host='https://niyan.example',
            paths=self.paths,
            stores=self.stores,
            api_factory=FakeDeviceApi,
            sleep=lambda seconds: None,
            browser_open=lambda url: True,
            stderr=output,
        )

        encoded_config = self.paths.global_config.read_text()
        self.assertNotIn('niyan_selector_secret', encoded_config)
        self.assertEqual(Configuration.load(self.paths.global_config).default_host, 'https://niyan.example')
        self.assertEqual(self.stores.keyring.get(host='https://niyan.example', token_id=binding.token_id), 'niyan_selector_secret')
        selected = select_credential(
            host='https://niyan.example',
            dataset_path='researcher/images',
            paths=self.paths,
            stores=self.stores,
            cwd=self.root,
        )
        self.assertEqual(selected.token, 'niyan_selector_secret')
        self.assertEqual(selected.source, 'user')

    def test_local_login_binding_is_not_selected_outside_checkout(self):
        """Keep checkout-local login unavailable from an unrelated directory."""

        login(
            host='https://niyan.example',
            paths=self.paths,
            stores=self.stores,
            local=True,
            cwd=self.repository,
            api_factory=FakeDeviceApi,
            sleep=lambda seconds: None,
            browser_open=lambda url: True,
            stderr=io.StringIO(),
        )

        local_path = find_local_config(self.repository, required=True)
        self.assertTrue(local_path.exists())
        self.assertFalse(self.paths.global_config.exists())
        self.assertEqual(resolve_host(None, paths=self.paths, cwd=self.repository, environment={}), 'https://niyan.example')
        selected = select_credential(
            host='https://niyan.example',
            dataset_path='researcher/images',
            paths=self.paths,
            stores=self.stores,
            cwd=self.repository,
        )
        self.assertEqual(selected.source, 'local')
        with self.assertRaises(CredentialError):
            select_credential(
                host='https://niyan.example',
                dataset_path='researcher/images',
                paths=self.paths,
                stores=self.stores,
                cwd=self.root,
            )
        with self.assertRaises(ConfigurationError):
            resolve_host(None, paths=self.paths, cwd=self.root, environment={})

    def test_exact_global_binding_precedes_checkout_local_default(self):
        """Prefer resource specificity before configuration location."""

        local_binding = CredentialBinding(token_id='11111111-1111-1111-1111-111111111111', username='local-user', storage='keyring')
        global_binding = CredentialBinding(
            token_id='22222222-2222-2222-2222-222222222222',
            username='dataset-user',
            storage='keyring',
            dataset_path='researcher/images',
        )
        local_configuration = Configuration()
        local_configuration.set_binding(host='https://niyan.example', binding=local_binding)
        local_configuration.save(find_local_config(self.repository, required=True))
        global_configuration = Configuration()
        global_configuration.set_binding(host='https://niyan.example', binding=global_binding)
        global_configuration.save(self.paths.global_config)
        self.stores.keyring.set(host='https://niyan.example', token_id=local_binding.token_id, token='local-secret')
        self.stores.keyring.set(host='https://niyan.example', token_id=global_binding.token_id, token='dataset-secret')

        selected = select_credential(
            host='https://niyan.example',
            dataset_path='researcher/images',
            paths=self.paths,
            stores=self.stores,
            cwd=self.repository,
        )

        self.assertEqual(selected.token, 'dataset-secret')
        self.assertEqual(selected.source, 'user')

    def test_missing_exact_credential_does_not_fall_back_to_broader_token(self):
        """Report a broken narrow binding instead of silently widening access."""

        exact_binding = CredentialBinding(
            token_id='33333333-3333-3333-3333-333333333333',
            username='dataset-user',
            storage='keyring',
            dataset_path='researcher/images',
        )
        broad_binding = CredentialBinding(token_id='44444444-4444-4444-4444-444444444444', username='broad-user', storage='keyring')
        configuration = Configuration()
        configuration.set_binding(host='https://niyan.example', binding=exact_binding)
        configuration.set_binding(host='https://niyan.example', binding=broad_binding)
        configuration.save(self.paths.global_config)
        self.stores.keyring.set(host='https://niyan.example', token_id=broad_binding.token_id, token='broad-secret')

        with self.assertRaises(CredentialError):
            select_credential(
                host='https://niyan.example',
                dataset_path='researcher/images',
                paths=self.paths,
                stores=self.stores,
                cwd=self.root,
            )

    def test_insecure_store_requires_private_file_permissions(self):
        """Refuse plaintext credentials if another user could read them."""

        store = InsecureFileCredentialStore(self.paths.insecure_credentials)
        store.set(host='https://niyan.example', token_id='token-id', token='secret')
        self.assertEqual(stat.S_IMODE(self.paths.insecure_credentials.stat().st_mode), 0o600)
        self.paths.insecure_credentials.chmod(0o644)

        with self.assertRaises(CredentialError):
            store.get(host='https://niyan.example', token_id='token-id')


class CliGitTests(unittest.TestCase):
    """Verify credential-helper isolation and Git clone orchestration."""

    def setUp(self):
        """Create isolated paths and one globally stored credential."""

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.paths = AppPaths(config_home=self.root / 'config', data_home=self.root / 'data')
        self.keyring = FakeKeyring()
        self.stores = CredentialStores(paths=self.paths, keyring_module=self.keyring)
        self.binding = CredentialBinding(token_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', username='researcher', storage='keyring')
        config = Configuration(default_host='https://niyan.example')
        config.set_binding(host='https://niyan.example', binding=self.binding)
        config.save(self.paths.global_config)
        self.stores.keyring.set(host='https://niyan.example', token_id=self.binding.token_id, token='niyan_selector_secret')

    def tearDown(self):
        """Remove isolated CLI state."""

        self.temporary_directory.cleanup()

    def test_credential_helper_rejects_another_host(self):
        """Never return a token to a redirected or reconfigured Git origin."""

        output = io.StringIO()
        status = credential_helper(
            host='https://niyan.example',
            username='researcher',
            token_id=self.binding.token_id,
            storage='keyring',
            paths=self.paths,
            stores=self.stores,
            stdin=io.StringIO('protocol=https\nhost=evil.example\n\n'),
            stdout=output,
        )

        self.assertEqual(status, 1)
        self.assertEqual(output.getvalue(), '')

    def test_clone_passes_only_non_secret_metadata_to_git(self):
        """Keep the access token out of Git arguments and child environment."""

        class CloneApi:
            """Return one same-origin repository location."""

            def __init__(self, host, token=None):
                self.host = host
                self.token = token

            def resolve_dataset(self, dataset_path):
                return 200, {
                    'id': 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
                    'path': 'researcher/images',
                    'name': 'Images',
                    'git_url': 'https://niyan.example/git/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb.git',
                }

        completed = subprocess.CompletedProcess(args=[], returncode=0)
        with patch('niyan.git.shutil.which', return_value='/usr/bin/git'), patch('niyan.git.subprocess.run', return_value=completed) as run:
            destination = clone_dataset(
                host='https://niyan.example',
                dataset_path='researcher/images',
                destination='checkout',
                paths=self.paths,
                stores=self.stores,
                cwd=self.root,
                environment={'NIYAN_TOKEN': 'environment-secret', 'PATH': os.environ.get('PATH', '')},
                api_factory=CloneApi,
                stderr=io.StringIO(),
            )

        command = run.call_args.args[0]
        child_environment = run.call_args.kwargs['env']
        self.assertEqual(destination, self.root / 'checkout')
        self.assertNotIn('niyan_selector_secret', repr(command))
        self.assertNotIn('environment-secret', repr(command))
        self.assertNotIn('NIYAN_TOKEN', child_environment)
        self.assertIn('credential.helper=', command[2])


class HostValidationTests(unittest.TestCase):
    """Verify transport security and host normalization rules."""

    def test_https_is_required_except_for_loopback(self):
        """Reject plaintext remote installations while allowing local development."""

        self.assertEqual(normalize_host('niyan.example'), 'https://niyan.example')
        self.assertEqual(normalize_host('http://127.0.0.1:8000'), 'http://127.0.0.1:8000')
        with self.assertRaises(ConfigurationError):
            normalize_host('http://niyan.example')
        with self.assertRaises(ConfigurationError):
            normalize_host('https://user:secret@niyan.example')


if __name__ == '__main__':
    unittest.main()
