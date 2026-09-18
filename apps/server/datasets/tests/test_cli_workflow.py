import io
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from django.test import LiveServerTestCase, override_settings

from accounts.models import AccessToken, User
from accounts.tokens import create_access_token
from datasets.tests.test_lfs_integration import DirectObjectHandler, NetworkedMemoryStore


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CLIENT_SOURCE = REPOSITORY_ROOT / 'clients' / 'python' / 'src'
if str(CLIENT_SOURCE) not in sys.path:
    sys.path.insert(0, str(CLIENT_SOURCE))

from niyan.config import AppPaths, Configuration, CredentialBinding
from niyan.credentials import CredentialStores
from niyan.datasets import create_remote_dataset, view_remote_dataset
from niyan.errors import ApiError, GitConflictError, GitError
from niyan.git import clone_dataset, pull_dataset, push_dataset
from niyan.working_copy import commit_changes, stage_paths


MIB = 1024 * 1024


class InterruptibleObjectHandler(DirectObjectHandler):
    """Let acceptance tests simulate a direct-transfer connection loss."""

    def do_PUT(self):
        """Drop the connection before reading bytes when interruption is enabled."""

        if self.server.interrupt_uploads:
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            return
        super().do_PUT()


class CliAlphaWorkflowTests(LiveServerTestCase):
    """Protect the complete Phase 2 workflow across CLI, Git, LFS, and Django."""

    def setUp(self):
        """Create isolated repositories, credentials, and a direct object service."""

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repositories = self.root / 'repositories'
        self.repositories.mkdir()
        self.workspaces = self.root / 'workspaces'
        self.workspaces.mkdir()
        self.settings_override = override_settings(REPOSITORIES_ROOT=self.repositories)
        self.settings_override.enable()

        self.provider = self._provider()
        self.provider_thread = Thread(target=self.provider.serve_forever, daemon=True)
        self.provider_thread.start()
        self.object_store = NetworkedMemoryStore(self.provider)
        self.store_patcher = patch('datasets.lfs_transfers.S3ObjectStore', return_value=self.object_store)
        self.store_patcher.start()

        self.user = User.objects.create_user(username='researcher')
        self.access_token, self.raw_token = create_access_token(
            user=self.user,
            name='CLI acceptance',
            scopes=['api', 'write_repository'],
            origin=AccessToken.Origin.CLI,
        )
        self.paths, self.stores = self._credential_state('researcher', self.access_token, self.raw_token, 'primary')
        self.environment = dict(os.environ)
        self.environment['XDG_CONFIG_HOME'] = str(self.paths.config_home.parent)
        self.environment['XDG_DATA_HOME'] = str(self.paths.data_home.parent)
        self.environment['PYTHONPATH'] = os.pathsep.join(filter(None, (str(CLIENT_SOURCE), self.environment.get('PYTHONPATH'))))

    def tearDown(self):
        """Stop provider services and remove every isolated checkout."""

        self.store_patcher.stop()
        self.provider.shutdown()
        self.provider.server_close()
        self.provider_thread.join(timeout=5)
        self.settings_override.disable()
        self.temporary_directory.cleanup()

    def test_publish_browse_clone_materialize_modify_and_pull(self):
        """Exercise the successful usable-alpha workflow with shallow history."""

        dataset, source = self._create_dataset('images')
        (source / 'README.md').write_text('# Research images\n')
        (source / 'labels.csv').write_text('sample,label\n1,cat\n')
        large_content = b'large dataset block\n' + b'\0' * (10 * MIB)
        (source / 'images.bin').write_bytes(large_content)
        stage_paths([], all_paths=True, cwd=source)
        commit_changes(message='Add initial dataset', cwd=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        push_dataset(paths=self.paths, stores=self.stores, cwd=source, environment=self.environment, stderr=io.StringIO())

        browse_output = io.StringIO()
        view_remote_dataset(
            host=self.live_server_url,
            dataset_path='researcher/images',
            web=False,
            paths=self.paths,
            stores=self.stores,
            cwd=self.workspaces,
            stdout=browse_output,
        )
        consumer = clone_dataset(
            host=self.live_server_url,
            dataset_path='researcher/images',
            destination='consumer',
            paths=self.paths,
            stores=self.stores,
            cwd=self.workspaces,
            environment=self.environment,
            stderr=io.StringIO(),
        )

        self.assertIn('# Research images', browse_output.getvalue())
        self.assertEqual((consumer / 'images.bin').read_bytes(), large_content)
        self.assertEqual(self._git('-C', str(consumer), 'rev-parse', '--is-shallow-repository').stdout.strip(), 'true')

        (source / 'labels.csv').write_text('sample,label\n1,cat\n2,dog\n')
        stage_paths(['labels.csv'], cwd=source)
        commit_changes(message='Update labels', cwd=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        push_dataset(paths=self.paths, stores=self.stores, cwd=source, environment=self.environment, stderr=io.StringIO())
        pull_dataset(paths=self.paths, stores=self.stores, cwd=consumer, environment=self.environment, stderr=io.StringIO())

        self.assertIn('2,dog', (consumer / 'labels.csv').read_text())
        self.assertEqual(self._git('-C', str(consumer), 'rev-list', '--count', 'HEAD').stdout.strip(), '1')
        self.assertEqual(dataset['default_branch'], 'main')

    def test_denied_clone_missing_content_and_interrupted_upload_preserve_remote(self):
        """Fail closed across authorization, missing cache content, and lost connections."""

        dataset, source = self._create_dataset('protected')
        (source / 'README.md').write_text('# Protected dataset\n')
        stage_paths([], all_paths=True, cwd=source)
        commit_changes(message='Initial dataset', cwd=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        push_dataset(paths=self.paths, stores=self.stores, cwd=source, environment=self.environment, stderr=io.StringIO())
        remote_before = self._remote_tip(dataset['id'])

        outsider = User.objects.create_user(username='outsider')
        outsider_token, outsider_raw = create_access_token(
            user=outsider,
            name='Outsider',
            scopes=['read_api', 'read_repository'],
            origin=AccessToken.Origin.CLI,
        )
        outsider_paths, outsider_stores = self._credential_state('outsider', outsider_token, outsider_raw, 'outsider')
        with self.assertRaises(ApiError) as denied:
            clone_dataset(
                host=self.live_server_url,
                dataset_path='researcher/protected',
                destination='denied',
                paths=outsider_paths,
                stores=outsider_stores,
                cwd=self.workspaces,
                environment=self.environment,
                stderr=io.StringIO(),
            )
        self.assertEqual(denied.exception.status, 404)

        interrupted_content = b'\0' * (10 * MIB + 1)
        interrupted_path = source / 'interrupted.bin'
        interrupted_path.write_bytes(interrupted_content)
        stage_paths(['interrupted.bin'], cwd=source)
        commit_changes(message='Add interrupted object', cwd=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.provider.interrupt_uploads = True
        with self.assertRaises(GitError):
            push_dataset(paths=self.paths, stores=self.stores, cwd=source, environment=self.environment, stderr=io.StringIO())
        self.assertEqual(self._remote_tip(dataset['id']), remote_before)

        self.provider.interrupt_uploads = False
        push_dataset(paths=self.paths, stores=self.stores, cwd=source, environment=self.environment, stderr=io.StringIO())
        published_tip = self._remote_tip(dataset['id'])
        missing_content = b'\1' + b'\0' * (10 * MIB)
        (source / 'missing-copy.bin').write_bytes(missing_content)
        stage_paths(['missing-copy.bin'], cwd=source)
        commit_changes(message='Reference missing local object', cwd=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pointer = self._git('-C', str(source), 'show', 'HEAD:missing-copy.bin').stdout
        oid = next(line.removeprefix('oid sha256:') for line in pointer.splitlines() if line.startswith('oid sha256:'))
        cache_object = source / '.git' / 'lfs' / 'objects' / oid[:2] / oid[2:4] / oid
        cache_object.unlink()

        with self.assertRaises(GitError):
            push_dataset(paths=self.paths, stores=self.stores, cwd=source, environment=self.environment, stderr=io.StringIO())
        self.assertEqual(self._remote_tip(dataset['id']), published_tip)

    def test_non_fast_forward_push_preserves_local_and_remote_tips(self):
        """Keep competing writers intact and require an explicit divergence resolution."""

        dataset, source = self._create_dataset('concurrent')
        (source / 'data.txt').write_text('initial\n')
        stage_paths([], all_paths=True, cwd=source)
        commit_changes(message='Initial dataset', cwd=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        push_dataset(paths=self.paths, stores=self.stores, cwd=source, environment=self.environment, stderr=io.StringIO())
        rival = clone_dataset(
            host=self.live_server_url,
            dataset_path='researcher/concurrent',
            destination='rival',
            paths=self.paths,
            stores=self.stores,
            cwd=self.workspaces,
            environment=self.environment,
            stderr=io.StringIO(),
        )
        self._configure_author(rival)

        (source / 'source.txt').write_text('source\n')
        stage_paths(['source.txt'], cwd=source)
        commit_changes(message='Source update', cwd=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        push_dataset(paths=self.paths, stores=self.stores, cwd=source, environment=self.environment, stderr=io.StringIO())
        remote_tip = self._remote_tip(dataset['id'])

        (rival / 'rival.txt').write_text('rival\n')
        stage_paths(['rival.txt'], cwd=rival)
        commit_changes(message='Rival update', cwd=rival, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rival_tip = self._git('-C', str(rival), 'rev-parse', 'HEAD').stdout.strip()
        with self.assertRaises(GitConflictError):
            push_dataset(paths=self.paths, stores=self.stores, cwd=rival, environment=self.environment, stderr=io.StringIO())

        self.assertEqual(self._remote_tip(dataset['id']), remote_tip)
        self.assertEqual(self._git('-C', str(rival), 'rev-parse', 'HEAD').stdout.strip(), rival_tip)

    def _create_dataset(self, slug):
        """Create and clone one empty dataset through the public client APIs."""

        with patch.dict(os.environ, self.environment, clear=False):
            dataset = create_remote_dataset(
                host=self.live_server_url,
                dataset_path=f'researcher/{slug}',
                name=slug.title(),
                clone=True,
                paths=self.paths,
                stores=self.stores,
                cwd=self.workspaces,
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )
        checkout = self.workspaces / slug
        self._configure_author(checkout)
        return dataset, checkout

    def _credential_state(self, username, access_token, raw_token, name):
        """Persist one isolated mode-0600 acceptance credential."""

        paths = AppPaths(config_home=self.root / name / 'config' / 'niyan', data_home=self.root / name / 'data' / 'niyan')
        stores = CredentialStores(paths=paths)
        binding = CredentialBinding(token_id=str(access_token.id), username=username, storage='file', scopes=tuple(access_token.scopes))
        configuration = Configuration(default_host=self.live_server_url)
        configuration.set_binding(host=self.live_server_url, binding=binding)
        configuration.save(paths.global_config)
        stores.file.set(host=self.live_server_url, token_id=binding.token_id, token=raw_token)
        return paths, stores

    def _configure_author(self, checkout):
        """Set deterministic repository-local commit identity."""

        self._git('-C', str(checkout), 'config', 'user.name', 'Niyān Acceptance')
        self._git('-C', str(checkout), 'config', 'user.email', 'acceptance@niyan.example')

    def _remote_tip(self, dataset_id):
        """Return the authoritative server branch tip."""

        return self._git('--git-dir', str(self.repositories / f'{dataset_id}.git'), 'rev-parse', 'refs/heads/main').stdout.strip()

    @staticmethod
    def _provider():
        """Create one loopback provider with controllable interruption state."""

        from http.server import ThreadingHTTPServer

        provider = ThreadingHTTPServer(('127.0.0.1', 0), InterruptibleObjectHandler)
        provider.objects = {}
        provider.valid_generations = {}
        provider.put_paths = []
        provider.get_paths = []
        provider.interrupt_uploads = False
        return provider

    @staticmethod
    def _git(*arguments):
        """Run deterministic Git inspection for acceptance assertions."""

        return subprocess.run(['git', *arguments], check=True, capture_output=True, text=True)
