import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from niyan.auth import authentication_status, login, login_with_token, logout, resolve_host, select_credential
from niyan.cli import _error_exit_status, _read_access_token, build_parser, main
from niyan.config import AppPaths, CheckoutIdentity, Configuration, CredentialBinding, find_local_config
from niyan.credentials import CredentialStores, InsecureFileCredentialStore
from niyan.datasets import create_remote_dataset, delete_remote_dataset, edit_dataset_access, edit_remote_dataset, grant_dataset_access, list_dataset_access, list_remote_datasets, revoke_dataset_access, view_remote_dataset
from niyan.errors import ApiError, ConfigurationError, CredentialError, GitConflictError, GitDependencyError, GitError
from niyan.git import clone_dataset, credential_helper, fetch_dataset, load_checkout_identity, pull_dataset, push_dataset
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


class FakeDatasetApi:
    """Simulate dataset forge endpoints through the public client boundary."""

    calls = []
    deleted = []
    grants = []

    def __init__(self, host, token=None):
        """Remember the host and bearer token selected by the command."""

        self.host = host
        self.token = token

    def resolve_namespace(self, namespace_path):
        """Resolve the one namespace used by command tests."""

        type(self).calls.append(('resolve_namespace', namespace_path, self.token))
        return 200, {'id': '11111111-1111-1111-1111-111111111111', 'path': namespace_path, 'name': 'Researcher', 'kind': 'personal'}

    def create_dataset(self, *, namespace_id, slug, name):
        """Return one created dataset representation."""

        type(self).calls.append(('create_dataset', namespace_id, slug, name))
        return 201, self._dataset(slug=slug, name=name)

    def list_datasets(self, *, namespace_id=None, limit=100, offset=0):
        """Return a deterministic visible dataset page."""

        type(self).calls.append(('list_datasets', namespace_id, limit, offset))
        return 200, {'count': 1, 'limit': limit, 'offset': offset, 'items': [self._dataset()]}

    def resolve_dataset(self, dataset_path):
        """Resolve mutable input to the test dataset UUID."""

        type(self).calls.append(('resolve_dataset', dataset_path, self.token))
        return 200, {
            'id': '22222222-2222-2222-2222-222222222222',
            'path': 'researcher/images',
            'name': 'Research Images',
            'git_url': 'https://niyan.example/git/22222222-2222-2222-2222-222222222222.git',
        }

    def get_dataset(self, dataset_id):
        """Return current dataset metadata."""

        type(self).calls.append(('get_dataset', dataset_id))
        return 200, self._dataset()

    def update_dataset(self, dataset_id, *, slug=None, name=None):
        """Return the requested mutable values in the updated representation."""

        type(self).calls.append(('update_dataset', dataset_id, slug, name))
        return 200, self._dataset(slug=slug or 'images', name=name or 'Research Images')

    def delete_dataset(self, dataset_id):
        """Record permanent deletion."""

        type(self).deleted.append(dataset_id)
        return 204, {}

    def list_dataset_grants(self, dataset_id):
        """Return current explicit grants."""

        type(self).calls.append(('list_dataset_grants', dataset_id))
        return 200, {'count': len(type(self).grants), 'items': [dict(grant) for grant in type(self).grants]}

    def create_dataset_grant(self, dataset_id, *, role, username=None, group_path=None):
        """Create one deterministic explicit grant."""

        principal_type = 'user' if username is not None else 'group'
        principal_label = username if username is not None else group_path
        grant = {'id': len(type(self).grants) + 1, 'dataset_id': dataset_id, 'principal_type': principal_type, 'principal_label': principal_label, 'user_id': 7 if username is not None else None, 'group_namespace_id': None if username is not None else '33333333-3333-3333-3333-333333333333', 'role': role, 'created_at': '2026-09-18T00:00:00Z', 'updated_at': '2026-09-18T00:00:00Z'}
        type(self).grants.append(grant)
        type(self).calls.append(('create_dataset_grant', dataset_id, principal_type, principal_label, role))
        return 201, dict(grant)

    def update_dataset_grant(self, dataset_id, grant_id, *, role):
        """Change one fake grant role."""

        grant = next(grant for grant in type(self).grants if grant['id'] == grant_id)
        grant['role'] = role
        type(self).calls.append(('update_dataset_grant', dataset_id, grant_id, role))
        return 200, dict(grant)

    def delete_dataset_grant(self, dataset_id, grant_id):
        """Remove one fake grant."""

        type(self).grants = [grant for grant in type(self).grants if grant['id'] != grant_id]
        type(self).calls.append(('delete_dataset_grant', dataset_id, grant_id))
        return 204, {}

    def get_repository_readme(self, dataset_id, *, revision='main'):
        """Return a small README summary."""

        type(self).calls.append(('get_repository_readme', dataset_id, revision))
        return 200, {'content': '# Research Images\n\nExample dataset.'}

    @staticmethod
    def _dataset(*, slug='images', name='Research Images'):
        """Build one complete dataset API representation."""

        return {
            'id': '22222222-2222-2222-2222-222222222222',
            'namespace_id': '11111111-1111-1111-1111-111111111111',
            'namespace_path': 'researcher',
            'slug': slug,
            'name': name,
            'default_branch': 'main',
            'role': 'owner',
            'created_at': '2026-09-17T00:00:00Z',
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

    def test_immutable_dataset_identity_finds_binding_after_path_rename(self):
        """Select a dataset token by UUID when its configured path is stale."""

        binding = CredentialBinding(
            token_id='55555555-5555-5555-5555-555555555555',
            username='dataset-user',
            storage='keyring',
            dataset_id='dddddddd-dddd-dddd-dddd-dddddddddddd',
            dataset_path='researcher/old-images',
            resource_boundary='dataset',
        )
        configuration = Configuration()
        configuration.set_binding(host='https://niyan.example', binding=binding)
        configuration.save(self.paths.global_config)
        self.stores.keyring.set(host='https://niyan.example', token_id=binding.token_id, token='renamed-dataset-secret')

        selected = select_credential(
            host='https://niyan.example',
            dataset_path='researcher/images',
            dataset_id='dddddddd-dddd-dddd-dddd-dddddddddddd',
            paths=self.paths,
            stores=self.stores,
            cwd=self.root,
        )

        self.assertEqual(selected.token, 'renamed-dataset-secret')
        self.assertEqual(selected.binding, binding)

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


class CliDatasetForgeTests(unittest.TestCase):
    """Verify path-oriented remote dataset management workflows."""

    def setUp(self):
        """Create isolated configuration with one user-level credential."""

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.paths = AppPaths(config_home=self.root / 'config', data_home=self.root / 'data')
        self.keyring = FakeKeyring()
        self.stores = CredentialStores(paths=self.paths, keyring_module=self.keyring)
        self.binding = CredentialBinding(token_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', username='researcher', storage='keyring')
        configuration = Configuration(default_host='https://niyan.example')
        configuration.set_binding(host='https://niyan.example', binding=self.binding)
        configuration.save(self.paths.global_config)
        self.stores.keyring.set(host='https://niyan.example', token_id=self.binding.token_id, token='niyan_selector_secret')
        FakeDatasetApi.grants = [
            {'id': 1, 'dataset_id': '22222222-2222-2222-2222-222222222222', 'principal_type': 'user', 'principal_label': 'colleague', 'user_id': 7, 'group_namespace_id': None, 'role': 'reader', 'created_at': '2026-09-18T00:00:00Z', 'updated_at': '2026-09-18T00:00:00Z'}
        ]

    def tearDown(self):
        """Remove isolated CLI state and fake API history."""

        self.temporary_directory.cleanup()
        FakeDatasetApi.calls = []
        FakeDatasetApi.deleted = []
        FakeDatasetApi.grants = []

    def test_create_resolves_namespace_and_prints_canonical_identity(self):
        """Keep human paths at the CLI boundary and UUIDs at the API boundary."""

        output = io.StringIO()

        dataset = create_remote_dataset(
            host='https://niyan.example',
            dataset_path='researcher/images',
            name='Research Images',
            clone=False,
            paths=self.paths,
            stores=self.stores,
            cwd=self.root,
            api_factory=FakeDatasetApi,
            stdout=output,
        )

        self.assertEqual(dataset['id'], '22222222-2222-2222-2222-222222222222')
        self.assertIn(('resolve_namespace', 'researcher', 'niyan_selector_secret'), FakeDatasetApi.calls)
        self.assertIn(('create_dataset', '11111111-1111-1111-1111-111111111111', 'images', 'Research Images'), FakeDatasetApi.calls)
        self.assertEqual(output.getvalue(), 'Created researcher/images (22222222-2222-2222-2222-222222222222)\n')

    def test_list_supports_all_visible_or_one_namespace(self):
        """Resolve optional namespace filters without exposing UUID arguments."""

        all_output = io.StringIO()
        namespace_output = io.StringIO()

        list_remote_datasets(host='https://niyan.example', namespace_path=None, limit=20, paths=self.paths, stores=self.stores, cwd=self.root, api_factory=FakeDatasetApi, stdout=all_output)
        list_remote_datasets(host='https://niyan.example', namespace_path='researcher', limit=10, paths=self.paths, stores=self.stores, cwd=self.root, api_factory=FakeDatasetApi, stdout=namespace_output)

        self.assertEqual(all_output.getvalue(), 'researcher/images\towner\tResearch Images\n')
        self.assertEqual(namespace_output.getvalue(), all_output.getvalue())
        self.assertIn(('list_datasets', None, 20, 0), FakeDatasetApi.calls)
        self.assertIn(('list_datasets', '11111111-1111-1111-1111-111111111111', 10, 0), FakeDatasetApi.calls)

    def test_view_prints_access_metadata_and_readme(self):
        """Render the server's effective role and repository README."""

        output = io.StringIO()

        view_remote_dataset(host='https://niyan.example', dataset_path='researcher/images', web=False, paths=self.paths, stores=self.stores, cwd=self.root, api_factory=FakeDatasetApi, stdout=output)

        rendered = output.getvalue()
        self.assertIn('ID: 22222222-2222-2222-2222-222222222222', rendered)
        self.assertIn('Default branch: main', rendered)
        self.assertIn('Access: owner', rendered)
        self.assertIn('# Research Images', rendered)

    def test_view_infers_immutable_checkout_identity_after_remote_rename(self):
        """Avoid depending on a stale mutable path inside an existing checkout."""

        checkout = self.root / 'checkout'
        subprocess.run(['git', 'init', '--initial-branch=main', str(checkout)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(checkout), 'remote', 'add', 'origin', 'https://niyan.example/git/22222222-2222-2222-2222-222222222222.git'], check=True)
        checkout_configuration = Configuration(default_host='https://niyan.example')
        checkout_configuration.set_checkout(
            CheckoutIdentity(
                host='https://niyan.example',
                dataset_id='22222222-2222-2222-2222-222222222222',
                dataset_path='researcher/old-images',
            )
        )
        checkout_configuration.save(find_local_config(checkout, required=True))

        view_remote_dataset(host='https://niyan.example', dataset_path=None, web=False, paths=self.paths, stores=self.stores, cwd=checkout, api_factory=FakeDatasetApi, stdin=io.StringIO(), stdout=io.StringIO())

        self.assertFalse(any(call[0] == 'resolve_dataset' for call in FakeDatasetApi.calls))
        self.assertEqual(Configuration.load(find_local_config(checkout, required=True)).checkout.dataset_path, 'researcher/images')

    def test_edit_updates_only_explicit_fields(self):
        """Send a partial metadata update after resolving the dataset path."""

        output = io.StringIO()

        dataset = edit_remote_dataset(
            host='https://niyan.example',
            dataset_path='researcher/images',
            slug='microscopy',
            name=None,
            paths=self.paths,
            stores=self.stores,
            cwd=self.root,
            api_factory=FakeDatasetApi,
            stdout=output,
        )

        self.assertEqual(dataset['slug'], 'microscopy')
        self.assertIn(('update_dataset', '22222222-2222-2222-2222-222222222222', 'microscopy', None), FakeDatasetApi.calls)
        self.assertIn('Updated researcher/microscopy', output.getvalue())

    def test_delete_requires_exact_non_interactive_confirmation(self):
        """Reject generic confirmation before invoking irreversible deletion."""

        with self.assertRaises(ConfigurationError):
            delete_remote_dataset(
                host='https://niyan.example',
                dataset_path='researcher/images',
                confirmation='yes',
                paths=self.paths,
                stores=self.stores,
                cwd=self.root,
                api_factory=FakeDatasetApi,
                stdin=io.StringIO(),
                stdout=io.StringIO(),
            )
        self.assertEqual(FakeDatasetApi.deleted, [])

        output = io.StringIO()
        delete_remote_dataset(
            host='https://niyan.example',
            dataset_path='researcher/images',
            confirmation='researcher/images',
            paths=self.paths,
            stores=self.stores,
            cwd=self.root,
            api_factory=FakeDatasetApi,
            stdin=io.StringIO(),
            stdout=output,
        )

        self.assertEqual(FakeDatasetApi.deleted, ['22222222-2222-2222-2222-222222222222'])
        self.assertEqual(output.getvalue(), 'Deleted researcher/images\n')

    def test_dataset_command_grammar_exposes_crud_options(self):
        """Keep the public parser aligned with the accepted forge surface."""

        create_arguments = build_parser().parse_args(['dataset', 'create', 'researcher/images', '--name', 'Images', '--clone', '--full-history'])
        clone_arguments = build_parser().parse_args(
            ['dataset', 'clone', 'researcher/images', '--full-history', '--include', 'raw/**', '--include', 'labels/*.csv', '--exclude', 'raw/tmp/**', '--metadata-only']
        )
        edit_arguments = build_parser().parse_args(['dataset', 'edit', 'researcher/images', '--slug', 'microscopy'])
        delete_arguments = build_parser().parse_args(['dataset', 'delete', 'researcher/images', '--confirm', 'researcher/images'])

        self.assertTrue(create_arguments.clone)
        self.assertTrue(create_arguments.full_history)
        self.assertTrue(clone_arguments.full_history)
        self.assertEqual(clone_arguments.include, ['raw/**', 'labels/*.csv'])
        self.assertEqual(clone_arguments.exclude, ['raw/tmp/**'])
        self.assertTrue(clone_arguments.metadata_only)
        self.assertEqual(edit_arguments.slug, 'microscopy')
        self.assertEqual(delete_arguments.confirm, 'researcher/images')

    def test_dataset_access_commands_manage_human_principals_and_label_explicit_roles(self):
        """List and mutate grants without asking users for database identifiers."""

        list_output = io.StringIO()
        list_dataset_access(host='https://niyan.example', dataset_path='researcher/images', paths=self.paths, stores=self.stores, cwd=self.root, api_factory=FakeDatasetApi, stdout=list_output)
        created = grant_dataset_access(host='https://niyan.example', dataset_path='researcher/images', principal_type='group', principal='/analysis/', role='contributor', paths=self.paths, stores=self.stores, cwd=self.root, api_factory=FakeDatasetApi, stdout=io.StringIO())
        updated = edit_dataset_access(host='https://niyan.example', dataset_path='researcher/images', principal_type='user', principal='colleague', role='maintainer', paths=self.paths, stores=self.stores, cwd=self.root, api_factory=FakeDatasetApi, stdout=io.StringIO())
        revoked = revoke_dataset_access(host='https://niyan.example', dataset_path='researcher/images', principal_type='group', principal='analysis', paths=self.paths, stores=self.stores, cwd=self.root, api_factory=FakeDatasetApi, stdout=io.StringIO())

        self.assertEqual(list_output.getvalue(), 'Your effective role: owner\nTYPE\tPRINCIPAL\tEXPLICIT ROLE\nuser\tcolleague\treader\n')
        self.assertEqual(created['principal_label'], 'analysis')
        self.assertEqual(updated['role'], 'maintainer')
        self.assertTrue(revoked['revoked'])
        self.assertEqual([grant['principal_label'] for grant in FakeDatasetApi.grants], ['colleague'])

    def test_dataset_access_json_output_is_stable_and_parser_requires_one_principal(self):
        """Support automation while keeping user and group selectors exclusive."""

        output = io.StringIO()
        result = list_dataset_access(host='https://niyan.example', dataset_path='researcher/images', paths=self.paths, stores=self.stores, json_output=True, cwd=self.root, api_factory=FakeDatasetApi, stdout=output)
        arguments = build_parser().parse_args(['dataset', 'access', 'grant', 'researcher/images', '--user', 'colleague', '--role', 'reader', '--json'])

        self.assertEqual(arguments.user, 'colleague')
        self.assertTrue(arguments.json)
        self.assertEqual(output.getvalue(), f'{json.dumps(result, ensure_ascii=False, sort_keys=True)}\n')

    def test_main_dispatches_dataset_access_with_human_principals(self):
        """Carry the public parser values through to the access workflow."""

        paths = AppPaths(config_home=Path('/tmp/config'), data_home=Path('/tmp/data'))
        stores = object()
        with patch('niyan.cli.AppPaths.from_environment', return_value=paths), patch('niyan.cli.CredentialStores', return_value=stores), patch('niyan.cli.resolve_host', return_value='https://niyan.example'), patch('niyan.cli.grant_dataset_access') as grant_call:
            status = main(['dataset', 'access', 'grant', 'researcher/images', '--group', 'research/vision', '--role', 'contributor', '--json'])

        self.assertEqual(status, 0)
        self.assertEqual(grant_call.call_args.kwargs['principal_type'], 'group')
        self.assertEqual(grant_call.call_args.kwargs['principal'], 'research/vision')
        self.assertEqual(grant_call.call_args.kwargs['role'], 'contributor')
        self.assertTrue(grant_call.call_args.kwargs['json_output'])


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
        with patch('niyan.git.shutil.which', return_value='/usr/bin/git'), patch('niyan.git.subprocess.run', return_value=completed) as run, patch('niyan.git._save_checkout_identity') as save_identity:
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

        clone_command = run.call_args_list[0].args[0]
        lfs_command = run.call_args_list[-1].args[0]
        child_environment = run.call_args_list[0].kwargs['env']
        self.assertEqual(destination, self.root / 'checkout')
        self.assertNotIn('niyan_selector_secret', repr(run.call_args_list))
        self.assertNotIn('niyan_environment_secret', repr(run.call_args_list))
        self.assertNotIn('NIYAN_TOKEN', child_environment)
        self.assertIn('credential.helper=', clone_command[2])
        self.assertIn('--depth=1', clone_command)
        self.assertIn('--single-branch', clone_command)
        self.assertIn('--no-tags', clone_command)
        self.assertEqual(lfs_command[-5:], ['lfs', 'pull', '--include=', '--exclude=', 'origin'])
        self.assertEqual(run.call_args_list[1].args[0][-4:], ['lfs', 'install', '--local', '--skip-smudge'])
        configured_names = [call.args[0][-2] for call in run.call_args_list[2:6]]
        configured_values = {call.args[0][-2]: call.args[0][-1] for call in run.call_args_list[2:6]}
        self.assertEqual(
            configured_names,
            [
                'lfs.customtransfer.niyan-multipart.path',
                'lfs.customtransfer.niyan-multipart.args',
                'lfs.customtransfer.niyan-multipart.concurrent',
                'lfs.customtransfer.niyan-multipart.direction',
            ],
        )
        self.assertEqual(configured_values['lfs.customtransfer.niyan-multipart.concurrent'], 'true')
        self.assertFalse(any('lfs.concurrenttransfers' in repr(call.args[0]) for call in run.call_args_list))
        self.assertEqual(save_identity.call_args.args[0], self.root / 'checkout')

    def test_full_history_clone_omits_shallow_fetch_options(self):
        """Allow the exceptional complete-history clone explicitly."""

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
        with patch('niyan.git.shutil.which', return_value='/usr/bin/git'), patch('niyan.git.subprocess.run', return_value=completed) as run, patch('niyan.git._save_checkout_identity') as save_identity:
            clone_dataset(
                host='https://niyan.example',
                dataset_path='researcher/images',
                destination='checkout',
                paths=self.paths,
                stores=self.stores,
                full_history=True,
                cwd=self.root,
                environment={'NIYAN_TOKEN': 'niyan_environment_secret', 'PATH': os.environ.get('PATH', '')},
                api_factory=CloneApi,
                stderr=io.StringIO(),
            )

        clone_command = run.call_args_list[0].args[0]
        self.assertNotIn('--depth=1', clone_command)
        self.assertNotIn('--single-branch', clone_command)
        self.assertNotIn('--no-tags', clone_command)
        self.assertEqual(save_identity.call_args.args[1].history, 'full')

    def test_metadata_only_clone_records_filters_without_requiring_git_lfs(self):
        """Keep pointer-only clones useful for later selected materialization."""

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
        with patch('niyan.auth.find_local_config', return_value=None), patch('niyan.git.shutil.which', side_effect=lambda executable: '/usr/bin/git' if executable == 'git' else None), patch('niyan.git.subprocess.run', return_value=completed) as run, patch('niyan.git._save_checkout_identity') as save_identity:
            clone_dataset(
                host='https://niyan.example',
                dataset_path='researcher/images',
                destination='checkout',
                paths=self.paths,
                stores=self.stores,
                include=['raw/**', 'labels/*.csv'],
                exclude=['raw/tmp/**'],
                metadata_only=True,
                cwd=self.root,
                environment={'PATH': os.environ.get('PATH', '')},
                api_factory=CloneApi,
                stderr=io.StringIO(),
            )

        self.assertEqual(run.call_count, 6)
        self.assertEqual(run.call_args_list[1].args[0][-2:], ['lfs.customtransfer.niyan-multipart.path', 'niyan'])
        identity = save_identity.call_args.args[1]
        self.assertEqual(identity.lfs_include, ('raw/**', 'labels/*.csv'))
        self.assertEqual(identity.lfs_exclude, ('raw/tmp/**',))

    def test_fetch_and_pull_use_saved_identity_authentication_and_selection(self):
        """Delegate network and materialization while persisting shallow policy."""

        identity = CheckoutIdentity(
            host='https://niyan.example',
            dataset_id='22222222-2222-2222-2222-222222222222',
            dataset_path='researcher/images',
            lfs_include=('images/**',),
            lfs_exclude=('images/tmp/**',),
        )
        completed = subprocess.CompletedProcess(args=[], returncode=0)
        with patch('niyan.auth.find_local_config', return_value=None), patch('niyan.git.load_checkout_identity', return_value=identity), patch('niyan.git.shutil.which', return_value='/usr/bin/tool'), patch('niyan.git._current_branch', return_value='main'), patch('niyan.git._current_upstream', return_value='origin/main'), patch('niyan.git._save_checkout_identity') as save_identity, patch('niyan.git.subprocess.run', return_value=completed) as run:
            fetch_dataset(paths=self.paths, stores=self.stores, cwd=self.root, environment={'PATH': '/usr/bin'}, stderr=io.StringIO())
            pull_dataset(paths=self.paths, stores=self.stores, cwd=self.root, environment={'PATH': '/usr/bin'}, stderr=io.StringIO())

        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn('fetch', commands[0])
        pull_commands = commands[1:]
        self.assertEqual([command[command.index('-C') + 2] for command in pull_commands], ['fetch', 'merge', 'fetch', 'lfs'])
        self.assertIn('--ff-only', pull_commands[1])
        self.assertIn('--depth=1', pull_commands[2])
        self.assertEqual(pull_commands[3][-3:], ['--include=images/**', '--exclude=images/tmp/**', 'origin'])
        self.assertEqual(save_identity.call_args.args[1].history, 'shallow')
        self.assertNotIn('niyan_selector_secret', repr(commands))

    def test_full_history_pull_unshallows_and_divergence_is_explicit(self):
        """Convert history deliberately and classify a failed fast-forward as conflict."""

        identity = CheckoutIdentity(
            host='https://niyan.example',
            dataset_id='22222222-2222-2222-2222-222222222222',
            dataset_path='researcher/images',
        )
        success = subprocess.CompletedProcess(args=[], returncode=0)
        with patch('niyan.auth.find_local_config', return_value=None), patch('niyan.git.load_checkout_identity', return_value=identity), patch('niyan.git.shutil.which', return_value='/usr/bin/tool'), patch('niyan.git._current_branch', return_value='main'), patch('niyan.git._current_upstream', return_value='origin/main'), patch('niyan.git._save_checkout_identity') as save_identity, patch('niyan.git.subprocess.run', return_value=success) as run:
            pull_dataset(paths=self.paths, stores=self.stores, full_history=True, metadata_only=True, cwd=self.root, environment={'PATH': '/usr/bin'}, stderr=io.StringIO())

        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn('--unshallow', commands[0])
        self.assertFalse(any('--depth=1' in command for command in commands))
        self.assertEqual(save_identity.call_args.args[1].history, 'full')

        failure = subprocess.CompletedProcess(args=[], returncode=1)
        with patch('niyan.auth.find_local_config', return_value=None), patch('niyan.git.load_checkout_identity', return_value=identity), patch('niyan.git.shutil.which', return_value='/usr/bin/tool'), patch('niyan.git._current_branch', return_value='main'), patch('niyan.git._current_upstream', return_value='origin/main'), patch('niyan.git.subprocess.run', side_effect=[success, failure]):
            with self.assertRaises(GitConflictError):
                pull_dataset(paths=self.paths, stores=self.stores, metadata_only=True, cwd=self.root, environment={'PATH': '/usr/bin'}, stderr=io.StringIO())

    def test_push_publishes_lfs_before_the_authenticated_ref_update(self):
        """Finish LFS publication first and suppress only the duplicate pre-push scan."""

        identity = CheckoutIdentity(
            host='https://niyan.example',
            dataset_id='22222222-2222-2222-2222-222222222222',
            dataset_path='researcher/images',
        )
        completed = subprocess.CompletedProcess(args=[], returncode=0)
        output = io.StringIO()
        with patch('niyan.auth.find_local_config', return_value=None), patch('niyan.git._require_checkout', return_value=identity), patch('niyan.git.shutil.which', return_value='/usr/bin/tool'), patch('niyan.git._current_branch', return_value='main'), patch('niyan.git._git_output', return_value='0123456789abcdef'), patch('niyan.git._optional_current_upstream', return_value='origin/main'), patch('niyan.git.configure_lfs_transfer') as configure, patch('niyan.git.subprocess.run', return_value=completed) as run:
            push_dataset(
                paths=self.paths,
                stores=self.stores,
                cwd=self.root,
                environment={'NIYAN_TOKEN': 'niyan_environment_secret', 'PATH': '/usr/bin'},
                stderr=output,
            )

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][-6:], ['-C', str(self.root), 'lfs', 'push', 'origin', 'main'])
        self.assertEqual(commands[1][-7:], ['-C', str(self.root), 'push', '--follow-tags', '--progress', 'origin', 'main:refs/heads/main'])
        self.assertNotIn('--set-upstream', commands[1])
        self.assertNotIn('niyan_selector_secret', repr(run.call_args_list))
        self.assertNotIn('niyan_environment_secret', repr(run.call_args_list))
        self.assertNotIn('NIYAN_TOKEN', run.call_args_list[0].kwargs['env'])
        self.assertEqual(run.call_args_list[0].kwargs['env']['GIT_LFS_FORCE_PROGRESS'], '1')
        self.assertNotIn('GIT_LFS_SKIP_PUSH', run.call_args_list[0].kwargs['env'])
        self.assertEqual(run.call_args_list[1].kwargs['env']['GIT_LFS_SKIP_PUSH'], '1')
        configure.assert_called_once_with(self.root, environment={'PATH': '/usr/bin', 'GIT_LFS_SKIP_SMUDGE': '1', 'GIT_LFS_FORCE_PROGRESS': '1'}, install_filters=True)
        self.assertEqual(output.getvalue(), 'Push complete.\n')

    def test_first_push_creates_same_named_upstream(self):
        """Create and record an origin upstream when the local branch has none."""

        identity = CheckoutIdentity(
            host='https://niyan.example',
            dataset_id='22222222-2222-2222-2222-222222222222',
            dataset_path='researcher/images',
        )
        completed = subprocess.CompletedProcess(args=[], returncode=0)
        with patch('niyan.auth.find_local_config', return_value=None), patch('niyan.git._require_checkout', return_value=identity), patch('niyan.git.shutil.which', return_value='/usr/bin/tool'), patch('niyan.git._current_branch', return_value='experiment'), patch('niyan.git._git_output', return_value='0123456789abcdef'), patch('niyan.git._optional_current_upstream', return_value=None), patch('niyan.git.configure_lfs_transfer'), patch('niyan.git.subprocess.run', return_value=completed) as run:
            push_dataset(paths=self.paths, stores=self.stores, cwd=self.root, environment={'PATH': '/usr/bin'}, stderr=io.StringIO())

        push_command = run.call_args_list[1].args[0]
        self.assertEqual(push_command[-8:], ['-C', str(self.root), 'push', '--follow-tags', '--progress', '--set-upstream', 'origin', 'experiment:refs/heads/experiment'])

    def test_push_forces_visible_lfs_and_git_progress_by_default(self):
        """Keep long-running transfers observable even when stderr is not detected as a TTY."""

        identity = CheckoutIdentity(
            host='https://niyan.example',
            dataset_id='22222222-2222-2222-2222-222222222222',
            dataset_path='researcher/images',
        )
        completed = subprocess.CompletedProcess(args=[], returncode=0)
        with patch('niyan.auth.find_local_config', return_value=None), patch('niyan.git._require_checkout', return_value=identity), patch('niyan.git.shutil.which', return_value='/usr/bin/tool'), patch('niyan.git._current_branch', return_value='main'), patch('niyan.git._git_output', return_value='0123456789abcdef'), patch('niyan.git._optional_current_upstream', return_value='origin/main'), patch('niyan.git.configure_lfs_transfer'), patch('niyan.git.subprocess.run', return_value=completed) as run:
            push_dataset(paths=self.paths, stores=self.stores, cwd=self.root, environment={'PATH': '/usr/bin'}, stderr=io.StringIO())

        self.assertEqual(run.call_args_list[0].kwargs['env']['GIT_LFS_FORCE_PROGRESS'], '1')
        self.assertFalse(run.call_args_list[0].kwargs['capture_output'])
        self.assertIn('--progress', run.call_args_list[1].args[0])

    def test_quiet_push_suppresses_lfs_and_git_progress(self):
        """Retain an explicit opt-out for scripts that do not want transfer output."""

        identity = CheckoutIdentity(
            host='https://niyan.example',
            dataset_id='22222222-2222-2222-2222-222222222222',
            dataset_path='researcher/images',
        )
        completed = subprocess.CompletedProcess(args=[], returncode=0)
        with patch('niyan.auth.find_local_config', return_value=None), patch('niyan.git._require_checkout', return_value=identity), patch('niyan.git.shutil.which', return_value='/usr/bin/tool'), patch('niyan.git._current_branch', return_value='main'), patch('niyan.git._git_output', return_value='0123456789abcdef'), patch('niyan.git._optional_current_upstream', return_value='origin/main'), patch('niyan.git.configure_lfs_transfer'), patch('niyan.git.subprocess.run', return_value=completed) as run:
            push_dataset(paths=self.paths, stores=self.stores, quiet=True, cwd=self.root, environment={'PATH': '/usr/bin'}, stderr=io.StringIO())

        self.assertNotIn('GIT_LFS_FORCE_PROGRESS', run.call_args_list[0].kwargs['env'])
        self.assertTrue(run.call_args_list[0].kwargs['capture_output'])
        self.assertNotIn('--progress', run.call_args_list[1].args[0])

    def test_failed_lfs_publication_never_attempts_a_ref_update(self):
        """Keep the remote branch unchanged when any required LFS object is unavailable."""

        identity = CheckoutIdentity(
            host='https://niyan.example',
            dataset_id='22222222-2222-2222-2222-222222222222',
            dataset_path='researcher/images',
        )
        failed = subprocess.CompletedProcess(args=[], returncode=1)
        with patch('niyan.auth.find_local_config', return_value=None), patch('niyan.git._require_checkout', return_value=identity), patch('niyan.git.shutil.which', return_value='/usr/bin/tool'), patch('niyan.git._current_branch', return_value='main'), patch('niyan.git._git_output', return_value='0123456789abcdef'), patch('niyan.git._optional_current_upstream', return_value='origin/main'), patch('niyan.git.configure_lfs_transfer'), patch('niyan.git.subprocess.run', return_value=failed) as run:
            with self.assertRaisesRegex(GitError, 'branch was not updated'):
                push_dataset(paths=self.paths, stores=self.stores, cwd=self.root, environment={'PATH': '/usr/bin'}, stderr=io.StringIO())

        self.assertEqual(run.call_count, 1)

    def test_rejected_ref_update_is_a_conflict_after_lfs_success(self):
        """Report remote concurrency or branch-policy refusal without claiming success."""

        identity = CheckoutIdentity(
            host='https://niyan.example',
            dataset_id='22222222-2222-2222-2222-222222222222',
            dataset_path='researcher/images',
        )
        success = subprocess.CompletedProcess(args=[], returncode=0)
        failure = subprocess.CompletedProcess(args=[], returncode=1)
        with patch('niyan.auth.find_local_config', return_value=None), patch('niyan.git._require_checkout', return_value=identity), patch('niyan.git.shutil.which', return_value='/usr/bin/tool'), patch('niyan.git._current_branch', return_value='main'), patch('niyan.git._git_output', return_value='0123456789abcdef'), patch('niyan.git._optional_current_upstream', return_value='origin/main'), patch('niyan.git.configure_lfs_transfer'), patch('niyan.git.subprocess.run', side_effect=[success, failure]):
            with self.assertRaises(GitConflictError):
                push_dataset(paths=self.paths, stores=self.stores, cwd=self.root, environment={'PATH': '/usr/bin'}, stderr=io.StringIO())

    def test_interrupted_lfs_publication_does_not_claim_success(self):
        """Translate interruption into an actionable failure before any ref update."""

        identity = CheckoutIdentity(
            host='https://niyan.example',
            dataset_id='22222222-2222-2222-2222-222222222222',
            dataset_path='researcher/images',
        )
        with patch('niyan.auth.find_local_config', return_value=None), patch('niyan.git._require_checkout', return_value=identity), patch('niyan.git.shutil.which', return_value='/usr/bin/tool'), patch('niyan.git._current_branch', return_value='main'), patch('niyan.git._git_output', return_value='0123456789abcdef'), patch('niyan.git._optional_current_upstream', return_value='origin/main'), patch('niyan.git.configure_lfs_transfer'), patch('niyan.git._run_git', side_effect=KeyboardInterrupt):
            with self.assertRaisesRegex(GitError, 'interrupted'):
                push_dataset(paths=self.paths, stores=self.stores, cwd=self.root, environment={'PATH': '/usr/bin'}, stderr=io.StringIO())


class GitSynchronizationTests(unittest.TestCase):
    """Verify shallow fast-forward behavior with real local Git repositories."""

    def setUp(self):
        """Create a file-protocol bare remote and depth-one checkout."""

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.source = self.root / 'source'
        self.remote = self.root / 'remote.git'
        self.checkout = self.root / 'checkout'
        self.paths = AppPaths(config_home=self.root / 'config', data_home=self.root / 'data')
        self.keyring = FakeKeyring()
        self.stores = CredentialStores(paths=self.paths, keyring_module=self.keyring)
        self.binding = CredentialBinding(token_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', username='researcher', storage='keyring')
        configuration = Configuration(default_host='https://niyan.example')
        configuration.set_binding(host='https://niyan.example', binding=self.binding)
        configuration.save(self.paths.global_config)
        self.stores.keyring.set(host='https://niyan.example', token_id=self.binding.token_id, token='niyan_selector_secret')
        self.identity = CheckoutIdentity(
            host='https://niyan.example',
            dataset_id='22222222-2222-2222-2222-222222222222',
            dataset_path='researcher/images',
        )

        self._git('init', '--initial-branch=main', str(self.source))
        self._git('-C', str(self.source), 'config', 'user.name', 'Niyān Test')
        self._git('-C', str(self.source), 'config', 'user.email', 'test@niyan.example')
        (self.source / 'data.txt').write_text('initial\n')
        self._git('-C', str(self.source), 'add', 'data.txt')
        self._git('-C', str(self.source), 'commit', '-m', 'Initial')
        self._git('clone', '--bare', str(self.source), str(self.remote))
        self._git('clone', '--depth=1', '--no-tags', f'file://{self.remote}', str(self.checkout))
        self._git('-C', str(self.checkout), 'config', 'user.name', 'Niyān Test')
        self._git('-C', str(self.checkout), 'config', 'user.email', 'test@niyan.example')
        self.fake_bin = self.root / 'bin'
        self.fake_bin.mkdir()
        fake_lfs = self.fake_bin / 'git-lfs'
        fake_lfs.write_text(f'#!{sys.executable}\nraise SystemExit(0)\n')
        fake_lfs.chmod(fake_lfs.stat().st_mode | stat.S_IXUSR)
        self.environment = dict(os.environ)
        self.environment['PATH'] = f'{self.fake_bin}{os.pathsep}{self.environment.get("PATH", "")}'

    def tearDown(self):
        """Remove all local Git fixtures."""

        self.temporary_directory.cleanup()

    def test_metadata_only_pull_fast_forwards_and_restores_depth_one(self):
        """Use Git ancestry checks while keeping only the new tip visible."""

        (self.source / 'data.txt').write_text('remote update\n')
        self._git('-C', str(self.source), 'add', 'data.txt')
        self._git('-C', str(self.source), 'commit', '-m', 'Remote update')
        self._git('-C', str(self.source), 'push', str(self.remote), 'main')

        with patch('niyan.git._require_checkout', return_value=self.identity), patch('niyan.git._save_checkout_identity'):
            pull_dataset(paths=self.paths, stores=self.stores, metadata_only=True, cwd=self.checkout, stderr=io.StringIO())

        self.assertEqual((self.checkout / 'data.txt').read_text(), 'remote update\n')
        self.assertEqual(self._git('-C', str(self.checkout), 'rev-list', '--count', 'HEAD').stdout.strip(), '1')
        self.assertEqual(self._git('-C', str(self.checkout), 'rev-parse', '--is-shallow-repository').stdout.strip(), 'true')

    def test_divergent_pull_preserves_local_commit(self):
        """Reject a competing remote tip without resetting local work."""

        (self.checkout / 'local.txt').write_text('local\n')
        self._git('-C', str(self.checkout), 'add', 'local.txt')
        self._git('-C', str(self.checkout), 'commit', '-m', 'Local update')
        local_commit = self._git('-C', str(self.checkout), 'rev-parse', 'HEAD').stdout.strip()
        (self.source / 'remote.txt').write_text('remote\n')
        self._git('-C', str(self.source), 'add', 'remote.txt')
        self._git('-C', str(self.source), 'commit', '-m', 'Remote update')
        self._git('-C', str(self.source), 'push', str(self.remote), 'main')

        with patch('niyan.git._require_checkout', return_value=self.identity), patch('niyan.git._save_checkout_identity'):
            with self.assertRaises(GitConflictError):
                pull_dataset(paths=self.paths, stores=self.stores, metadata_only=True, cwd=self.checkout, stderr=io.StringIO())

        self.assertEqual(self._git('-C', str(self.checkout), 'rev-parse', 'HEAD').stdout.strip(), local_commit)
        self.assertTrue((self.checkout / 'local.txt').exists())
        self.assertFalse((self.checkout / 'remote.txt').exists())

    def test_push_updates_the_remote_only_after_lfs_succeeds(self):
        """Publish a real local Git ref through the same ordered client workflow."""

        (self.checkout / 'local.txt').write_text('local update\n')
        self._git('-C', str(self.checkout), 'add', 'local.txt')
        self._git('-C', str(self.checkout), 'commit', '-m', 'Local update')

        with patch('niyan.git._require_checkout', return_value=self.identity), patch('niyan.git.shutil.which', return_value='/usr/bin/tool'):
            push_dataset(paths=self.paths, stores=self.stores, cwd=self.checkout, environment=self.environment, stderr=io.StringIO())

        self.assertEqual(self._git('--git-dir', str(self.remote), 'show', 'main:local.txt').stdout, 'local update\n')

    def test_push_publishes_reachable_annotated_tags(self):
        """Carry ordinary annotated dataset releases with the current branch."""

        (self.checkout / 'release.txt').write_text('release\n')
        self._git('-C', str(self.checkout), 'add', 'release.txt')
        self._git('-C', str(self.checkout), 'commit', '-m', 'Release data')
        self._git('-C', str(self.checkout), 'tag', '--annotate', '--message', 'Version 1', 'v1')

        with patch('niyan.git._require_checkout', return_value=self.identity), patch('niyan.git.shutil.which', return_value='/usr/bin/tool'):
            push_dataset(paths=self.paths, stores=self.stores, cwd=self.checkout, environment=self.environment, stderr=io.StringIO())

        self.assertEqual(self._git('--git-dir', str(self.remote), 'cat-file', '-t', 'refs/tags/v1').stdout.strip(), 'tag')

    def test_non_fast_forward_push_preserves_both_local_and_remote_tips(self):
        """Delegate expected-old-object rejection to Git without overwriting either side."""

        (self.checkout / 'local.txt').write_text('local update\n')
        self._git('-C', str(self.checkout), 'add', 'local.txt')
        self._git('-C', str(self.checkout), 'commit', '-m', 'Local update')
        local_commit = self._git('-C', str(self.checkout), 'rev-parse', 'HEAD').stdout.strip()
        (self.source / 'remote.txt').write_text('remote update\n')
        self._git('-C', str(self.source), 'add', 'remote.txt')
        self._git('-C', str(self.source), 'commit', '-m', 'Remote update')
        self._git('-C', str(self.source), 'push', str(self.remote), 'main')
        remote_commit = self._git('--git-dir', str(self.remote), 'rev-parse', 'main').stdout.strip()

        with patch('niyan.git._require_checkout', return_value=self.identity), patch('niyan.git.shutil.which', return_value='/usr/bin/tool'):
            with self.assertRaises(GitConflictError):
                push_dataset(paths=self.paths, stores=self.stores, cwd=self.checkout, environment=self.environment, stderr=io.StringIO())

        self.assertEqual(self._git('-C', str(self.checkout), 'rev-parse', 'HEAD').stdout.strip(), local_commit)
        self.assertEqual(self._git('--git-dir', str(self.remote), 'rev-parse', 'main').stdout.strip(), remote_commit)

    def _git(self, *arguments):
        """Run one local Git fixture command."""

        return subprocess.run(['git', *arguments], check=True, capture_output=True, text=True)


class CheckoutIdentityTests(unittest.TestCase):
    """Verify checkout identity remains portable and detects remote drift."""

    def setUp(self):
        """Create a real Git checkout with matching Niyān metadata."""

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.checkout = self.root / 'checkout'
        subprocess.run(['git', 'init', '--initial-branch=main', str(self.checkout)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.checkout), 'remote', 'add', 'origin', 'https://niyan.example/git/22222222-2222-2222-2222-222222222222.git'], check=True)
        configuration = Configuration()
        configuration.set_checkout(
            CheckoutIdentity(
                host='https://niyan.example',
                dataset_id='22222222-2222-2222-2222-222222222222',
                dataset_path='researcher/images',
            )
        )
        configuration.save(find_local_config(self.checkout, required=True))

    def tearDown(self):
        """Remove the isolated checkout."""

        self.temporary_directory.cleanup()

    def test_checkout_identity_survives_worktree_move(self):
        """Discover identity through Git instead of an absolute worktree path."""

        moved_checkout = self.root / 'moved-checkout'
        shutil.move(self.checkout, moved_checkout)

        identity = load_checkout_identity(moved_checkout)

        self.assertEqual(identity.dataset_id, '22222222-2222-2222-2222-222222222222')
        self.assertEqual(identity.dataset_path, 'researcher/images')

    def test_mismatched_remote_is_rejected(self):
        """Do not apply one dataset identity to another Git remote."""

        subprocess.run(['git', '-C', str(self.checkout), 'remote', 'set-url', 'origin', 'https://niyan.example/git/33333333-3333-3333-3333-333333333333.git'], check=True)

        with self.assertRaisesRegex(GitError, 'does not match'):
            load_checkout_identity(self.checkout)

    def test_malformed_checkout_metadata_is_rejected(self):
        """Fail safely instead of guessing around corrupt local identity."""

        config_path = find_local_config(self.checkout, required=True)
        config_path.write_text('{"version": 1, "hosts": {}, "checkout": {"host": "https://niyan.example", "dataset_id": "not-a-uuid", "dataset_path": "researcher/images"}}')

        with self.assertRaises(ConfigurationError):
            load_checkout_identity(self.checkout)

    def test_checkout_identity_round_trips_lfs_materialization_selection(self):
        """Persist repeatable filters as non-secret checkout policy."""

        config_path = find_local_config(self.checkout, required=True)
        configuration = Configuration.load(config_path)
        configuration.set_checkout(
            CheckoutIdentity(
                host='https://niyan.example',
                dataset_id='22222222-2222-2222-2222-222222222222',
                dataset_path='researcher/images',
                lfs_include=('raw/**', 'labels/*.csv'),
                lfs_exclude=('raw/tmp/**',),
            )
        )
        configuration.save(config_path)

        loaded = Configuration.load(config_path).checkout

        self.assertEqual(loaded.lfs_include, ('raw/**', 'labels/*.csv'))
        self.assertEqual(loaded.lfs_exclude, ('raw/tmp/**',))
        self.assertNotIn('metadata_only', config_path.read_text())

    def test_fetch_and_pull_command_grammar_exposes_materialization_controls(self):
        """Keep synchronization options aligned with the accepted public CLI."""

        fetch_arguments = build_parser().parse_args(['fetch'])
        pull_arguments = build_parser().parse_args(['pull', '--full-history', '--include', 'raw/**', '--exclude', 'tmp/**', '--metadata-only'])
        push_arguments = build_parser().parse_args(['push'])

        self.assertEqual(fetch_arguments.command, 'fetch')
        self.assertTrue(pull_arguments.full_history)
        self.assertEqual(pull_arguments.include, ['raw/**'])
        self.assertEqual(pull_arguments.exclude, ['tmp/**'])
        self.assertTrue(pull_arguments.metadata_only)
        self.assertEqual(push_arguments.command, 'push')
        self.assertEqual(_error_exit_status(GitConflictError('diverged')), 6)
        self.assertEqual(_error_exit_status(GitDependencyError('missing')), 8)


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
