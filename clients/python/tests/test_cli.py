import io
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from niyan.auth import authentication_status, login, login_with_token, logout, resolve_host, select_credential
from niyan.cli import _read_access_token, build_parser, main
from niyan.config import AppPaths, Configuration, CredentialBinding, find_local_config
from niyan.credentials import CredentialStores, InsecureFileCredentialStore
from niyan.errors import ApiError, ConfigurationError, CredentialError
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
                'resource_boundary': 'user',
                'dataset_id': None,
                'dataset_path': None,
                'scopes': ['api', 'write_repository'],
                'expires_at': '2027-09-17T00:00:00Z',
                'active': True,
            },
        }

    def revoke_access_token(self, token_id):
        """Accept cleanup when device login cannot persist the credential."""

        return 204, {}


class FakeLifecycleApi:
    """Simulate token inspection and self-revocation."""

    revoked = []

    def __init__(self, host, token=None):
        """Remember selected host and bearer token."""

        self.host = host
        self.token = token

    def current_user(self):
        """Return metadata derived from the supplied test token."""

        if self.token == 'niyan_rejected_secret':
            raise ApiError('The access token is invalid or expired.', status=401, code='unauthorized')
        metadata_by_token = {
            'niyan_user_secret': {
                'id': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
                'resource_boundary': 'user',
                'dataset_id': None,
                'dataset_path': None,
            },
            'niyan_second_secret': {
                'id': 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
                'resource_boundary': 'user',
                'dataset_id': None,
                'dataset_path': None,
            },
            'niyan_dataset_secret': {
                'id': 'cccccccc-cccc-cccc-cccc-cccccccccccc',
                'resource_boundary': 'dataset',
                'dataset_id': 'dddddddd-dddd-dddd-dddd-dddddddddddd',
                'dataset_path': 'researcher/images',
            },
        }
        metadata = metadata_by_token.get(self.token)
        if metadata is None:
            raise ApiError('The access token is invalid or expired.', status=401, code='unauthorized')
        return 200, {
            'username': 'researcher',
            'access_token': {
                **metadata,
                'scopes': ['api', 'write_repository'],
                'expires_at': '2027-09-17T00:00:00Z',
                'active': True,
            },
        }

    def revoke_access_token(self, token_id):
        """Record self-revocation or simulate an unavailable server."""

        if self.token == 'niyan_unreachable_secret':
            raise ApiError('Could not reach the Niyān installation.', status=None)
        type(self).revoked.append((self.token, token_id))
        return 204, {}


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
        FakeLifecycleApi.revoked = []

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

    def test_manual_token_login_persists_server_metadata(self):
        """Store an inspected manual token without writing its secret to config."""

        binding = login_with_token(
            host='https://niyan.example',
            raw_token='niyan_user_secret\n',
            paths=self.paths,
            stores=self.stores,
            cwd=self.root,
            api_factory=FakeLifecycleApi,
            stderr=io.StringIO(),
        )

        configuration = Configuration.load(self.paths.global_config)
        saved = configuration.select_binding(host='https://niyan.example')
        self.assertEqual(saved, binding)
        self.assertEqual(binding.resource_boundary, 'user')
        self.assertEqual(binding.scopes, ('api', 'write_repository'))
        self.assertEqual(binding.expires_at, '2027-09-17T00:00:00Z')
        self.assertNotIn('niyan_user_secret', self.paths.global_config.read_text())

    def test_manual_dataset_token_infers_and_checks_its_boundary(self):
        """Bind manual dataset tokens only to their server-enforced path."""

        binding = login_with_token(
            host='https://niyan.example',
            raw_token='niyan_dataset_secret',
            paths=self.paths,
            stores=self.stores,
            dataset_path='researcher/images',
            cwd=self.root,
            api_factory=FakeLifecycleApi,
            stderr=io.StringIO(),
        )

        self.assertEqual(binding.dataset_path, 'researcher/images')
        self.assertIsNone(Configuration.load(self.paths.global_config).select_binding(host='https://niyan.example'))
        self.assertEqual(Configuration.load(self.paths.global_config).select_binding(host='https://niyan.example', dataset_path='researcher/images', include_default=False), binding)
        with self.assertRaises(CredentialError):
            login_with_token(
                host='https://niyan.example',
                raw_token='niyan_dataset_secret',
                paths=self.paths,
                stores=self.stores,
                dataset_path='researcher/other',
                cwd=self.root,
                api_factory=FakeLifecycleApi,
                stderr=io.StringIO(),
            )

    def test_authentication_status_reports_acceptance_without_secret(self):
        """Show useful server and binding metadata without exposing the token."""

        login_with_token(
            host='https://niyan.example',
            raw_token='niyan_user_secret',
            paths=self.paths,
            stores=self.stores,
            cwd=self.root,
            api_factory=FakeLifecycleApi,
            stderr=io.StringIO(),
        )
        output = io.StringIO()

        accepted = authentication_status(
            host='https://niyan.example',
            paths=self.paths,
            stores=self.stores,
            cwd=self.root,
            environment={},
            api_factory=FakeLifecycleApi,
            stdout=output,
        )

        self.assertTrue(accepted)
        self.assertIn('Account: researcher', output.getvalue())
        self.assertIn('Scopes: api, write_repository', output.getvalue())
        self.assertIn('Status: accepted', output.getvalue())
        self.assertNotIn('niyan_user_secret', output.getvalue())

    def test_authentication_status_reports_rejected_stored_credential(self):
        """Retain local metadata while reporting server rejection."""

        login_with_token(
            host='https://niyan.example',
            raw_token='niyan_user_secret',
            paths=self.paths,
            stores=self.stores,
            cwd=self.root,
            api_factory=FakeLifecycleApi,
            stderr=io.StringIO(),
        )
        binding = Configuration.load(self.paths.global_config).select_binding(host='https://niyan.example')
        self.stores.keyring.set(host='https://niyan.example', token_id=binding.token_id, token='niyan_rejected_secret')
        output = io.StringIO()

        accepted = authentication_status(
            host='https://niyan.example',
            paths=self.paths,
            stores=self.stores,
            cwd=self.root,
            environment={},
            api_factory=FakeLifecycleApi,
            stdout=output,
        )

        self.assertFalse(accepted)
        self.assertIn('Status: rejected', output.getvalue())
        self.assertIn('Expires: 2027-09-17T00:00:00Z', output.getvalue())
        self.assertNotIn('niyan_rejected_secret', output.getvalue())

    def test_logout_revokes_before_removing_local_state(self):
        """Remove a binding and secret only after successful self-revocation."""

        binding = login_with_token(
            host='https://niyan.example',
            raw_token='niyan_user_secret',
            paths=self.paths,
            stores=self.stores,
            cwd=self.root,
            api_factory=FakeLifecycleApi,
            stderr=io.StringIO(),
        )

        logout(
            host='https://niyan.example',
            paths=self.paths,
            stores=self.stores,
            cwd=self.root,
            api_factory=FakeLifecycleApi,
            stderr=io.StringIO(),
        )

        self.assertEqual(FakeLifecycleApi.revoked, [('niyan_user_secret', binding.token_id)])
        self.assertIsNone(Configuration.load(self.paths.global_config).select_binding(host='https://niyan.example'))
        with self.assertRaises(CredentialError):
            self.stores.keyring.get(host='https://niyan.example', token_id=binding.token_id)

    def test_failed_logout_keeps_binding_and_secret_for_recovery(self):
        """Avoid losing the only credential when server revocation fails."""

        binding = CredentialBinding(token_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', username='researcher', storage='keyring')
        configuration = Configuration(default_host='https://niyan.example')
        configuration.set_binding(host='https://niyan.example', binding=binding)
        configuration.save(self.paths.global_config)
        self.stores.keyring.set(host='https://niyan.example', token_id=binding.token_id, token='niyan_unreachable_secret')

        with self.assertRaises(ApiError):
            logout(
                host='https://niyan.example',
                paths=self.paths,
                stores=self.stores,
                cwd=self.root,
                api_factory=FakeLifecycleApi,
                stderr=io.StringIO(),
            )

        self.assertEqual(Configuration.load(self.paths.global_config).select_binding(host='https://niyan.example'), binding)
        self.assertEqual(self.stores.keyring.get(host='https://niyan.example', token_id=binding.token_id), 'niyan_unreachable_secret')

    def test_forget_removes_an_unusable_credential_without_server_call(self):
        """Provide an explicit local recovery path for unreachable credentials."""

        binding = CredentialBinding(token_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', username='researcher', storage='keyring')
        configuration = Configuration(default_host='https://niyan.example')
        configuration.set_binding(host='https://niyan.example', binding=binding)
        configuration.save(self.paths.global_config)
        output = io.StringIO()

        logout(
            host='https://niyan.example',
            paths=self.paths,
            stores=self.stores,
            forget=True,
            cwd=self.root,
            api_factory=FakeLifecycleApi,
            stderr=output,
        )

        self.assertEqual(FakeLifecycleApi.revoked, [])
        self.assertIsNone(Configuration.load(self.paths.global_config).select_binding(host='https://niyan.example'))
        self.assertIn('without revoking', output.getvalue())

    def test_local_logout_does_not_remove_global_binding(self):
        """Keep user-level credentials when forgetting a checkout binding."""

        global_binding = login_with_token(
            host='https://niyan.example',
            raw_token='niyan_user_secret',
            paths=self.paths,
            stores=self.stores,
            cwd=self.root,
            api_factory=FakeLifecycleApi,
            stderr=io.StringIO(),
        )
        login_with_token(
            host='https://niyan.example',
            raw_token='niyan_second_secret',
            paths=self.paths,
            stores=self.stores,
            local=True,
            cwd=self.repository,
            api_factory=FakeLifecycleApi,
            stderr=io.StringIO(),
        )

        logout(
            host='https://niyan.example',
            paths=self.paths,
            stores=self.stores,
            local=True,
            forget=True,
            cwd=self.repository,
            api_factory=FakeLifecycleApi,
            stderr=io.StringIO(),
        )

        self.assertEqual(Configuration.load(self.paths.global_config).select_binding(host='https://niyan.example'), global_binding)

    def test_dataset_logout_removes_only_the_exact_binding(self):
        """Keep a broad credential when forgetting one dataset token."""

        global_binding = login_with_token(
            host='https://niyan.example',
            raw_token='niyan_user_secret',
            paths=self.paths,
            stores=self.stores,
            cwd=self.root,
            api_factory=FakeLifecycleApi,
            stderr=io.StringIO(),
        )
        login_with_token(
            host='https://niyan.example',
            raw_token='niyan_dataset_secret',
            paths=self.paths,
            stores=self.stores,
            dataset_path='researcher/images',
            cwd=self.root,
            api_factory=FakeLifecycleApi,
            stderr=io.StringIO(),
        )

        logout(
            host='https://niyan.example',
            paths=self.paths,
            stores=self.stores,
            dataset_path='researcher/images',
            forget=True,
            cwd=self.root,
            api_factory=FakeLifecycleApi,
            stderr=io.StringIO(),
        )

        configuration = Configuration.load(self.paths.global_config)
        self.assertEqual(configuration.select_binding(host='https://niyan.example'), global_binding)
        self.assertIsNone(configuration.select_binding(host='https://niyan.example', dataset_path='researcher/images', include_default=False))


class CliAuthenticationCommandTests(unittest.TestCase):
    """Verify authentication command grammar and secret input handling."""

    def test_authentication_commands_accept_lifecycle_selectors(self):
        """Expose dataset and local selectors on status and logout."""

        status = build_parser().parse_args(['auth', 'status', '--host', 'niyan.example', '--dataset', 'researcher/images'])
        logout_arguments = build_parser().parse_args(['auth', 'logout', '--host', 'niyan.example', '--local', '--dataset', 'researcher/images', '--forget'])

        self.assertEqual(status.dataset, 'researcher/images')
        self.assertTrue(logout_arguments.local)
        self.assertTrue(logout_arguments.forget)

    def test_manual_token_is_read_from_stdin_or_non_echoing_prompt(self):
        """Keep manually issued tokens out of command-line arguments."""

        piped = _read_access_token(stdin=io.StringIO('niyan_user_secret\n'))

        class InteractiveInput(io.StringIO):
            """Present a terminal-like standard input test double."""

            def isatty(self):
                """Report an interactive terminal."""

                return True

        prompted = _read_access_token(stdin=InteractiveInput(), secret_prompt=lambda prompt: 'niyan_prompted_secret')

        self.assertEqual(piped, 'niyan_user_secret\n')
        self.assertEqual(prompted, 'niyan_prompted_secret')

    def test_main_dispatches_login_status_and_logout(self):
        """Connect the public command grammar to each lifecycle operation."""

        paths = AppPaths(config_home=Path('/tmp/config'), data_home=Path('/tmp/data'))
        stores = object()
        with patch('niyan.cli.AppPaths.from_environment', return_value=paths), patch('niyan.cli.CredentialStores', return_value=stores), patch('niyan.cli.resolve_host', return_value='https://niyan.example'), patch('niyan.cli.login_with_token') as login_call, patch('niyan.cli.authentication_status', side_effect=(True, False)) as status_call, patch('niyan.cli.logout') as logout_call, patch('niyan.cli.sys.stdin', io.StringIO('niyan_user_secret\n')):
            login_status = main(['auth', 'login', '--with-token'])
            accepted_status = main(['auth', 'status'])
            rejected_status = main(['auth', 'status'])
            logout_status = main(['auth', 'logout', '--forget'])

        self.assertEqual(login_status, 0)
        self.assertEqual(accepted_status, 0)
        self.assertEqual(rejected_status, 3)
        self.assertEqual(logout_status, 0)
        self.assertEqual(login_call.call_args.kwargs['raw_token'], 'niyan_user_secret\n')
        self.assertEqual(status_call.call_count, 2)
        self.assertTrue(logout_call.call_args.kwargs['forget'])


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
                environment={'NIYAN_TOKEN': 'niyan_environment_secret', 'PATH': os.environ.get('PATH', '')},
                api_factory=CloneApi,
                stderr=io.StringIO(),
            )

        command = run.call_args.args[0]
        child_environment = run.call_args.kwargs['env']
        self.assertEqual(destination, self.root / 'checkout')
        self.assertNotIn('niyan_selector_secret', repr(command))
        self.assertNotIn('niyan_environment_secret', repr(command))
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
