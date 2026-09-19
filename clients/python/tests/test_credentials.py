import json
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from niyan.credentials import CredentialStores, InsecureFileCredentialStore, KeyringCredentialStore
from niyan.errors import CredentialError


class KeyringCredentialStoreTests(unittest.TestCase):
    """Verify secure-store failures are translated without leaking details."""

    def test_keyring_round_trip_uses_host_scoped_service(self):
        """Persist, retrieve, and delete a token under its installation origin."""

        module = Mock()
        module.get_password.return_value = 'secret-token'
        store = KeyringCredentialStore(module=module)

        store.set(host='https://data.example.test', token_id='token-id', token='secret-token')
        self.assertEqual(store.get(host='https://data.example.test', token_id='token-id'), 'secret-token')
        store.delete(host='https://data.example.test', token_id='token-id')

        module.set_password.assert_called_once_with('niyan:https://data.example.test', 'token-id', 'secret-token')
        module.get_password.assert_called_once_with('niyan:https://data.example.test', 'token-id')
        module.delete_password.assert_called_once_with('niyan:https://data.example.test', 'token-id')

    def test_keyring_write_and_read_failures_are_actionable(self):
        """Map backend faults and missing secrets to distinct public errors."""

        module = Mock()
        store = KeyringCredentialStore(module=module)
        module.set_password.side_effect = RuntimeError('private backend detail')
        with self.assertRaisesRegex(CredentialError, 'rejected the token'):
            store.set(host='https://data.example.test', token_id='token-id', token='secret')

        module.get_password.side_effect = RuntimeError('private backend detail')
        with self.assertRaisesRegex(CredentialError, 'store is unavailable'):
            store.get(host='https://data.example.test', token_id='token-id')

        module.get_password.side_effect = None
        module.get_password.return_value = None
        with self.assertRaisesRegex(CredentialError, 'credential is missing'):
            store.get(host='https://data.example.test', token_id='token-id')

    def test_keyring_delete_is_idempotent_when_backend_fails(self):
        """Allow logout cleanup to proceed when a secret is already unavailable."""

        module = Mock()
        module.delete_password.side_effect = RuntimeError('already gone')

        KeyringCredentialStore(module=module).delete(host='https://data.example.test', token_id='token-id')


class InsecureFileCredentialStoreTests(unittest.TestCase):
    """Verify explicit plaintext storage is atomic and permission checked."""

    def setUp(self):
        """Create one isolated credential file path."""

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / 'nested' / 'credentials.json'
        self.store = InsecureFileCredentialStore(self.path)

    def tearDown(self):
        """Remove the isolated credential directory."""

        self.temporary_directory.cleanup()

    def test_plaintext_round_trip_uses_restrictive_permissions(self):
        """Write, retrieve, and delete only the selected host-token pair."""

        self.store.set(host='https://one.example', token_id='same-id', token='one-secret')
        self.store.set(host='https://two.example', token_id='same-id', token='two-secret')

        self.assertEqual(self.store.get(host='https://one.example', token_id='same-id'), 'one-secret')
        self.assertEqual(self.store.get(host='https://two.example', token_id='same-id'), 'two-secret')
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertIn('https://one.example\nsame-id', json.loads(self.path.read_text())['tokens'])

        self.store.delete(host='https://one.example', token_id='same-id')
        with self.assertRaisesRegex(CredentialError, 'credential is missing'):
            self.store.get(host='https://one.example', token_id='same-id')
        self.assertEqual(self.store.get(host='https://two.example', token_id='same-id'), 'two-secret')
        original = self.path.read_text()
        self.store.delete(host='https://missing.example', token_id='missing')
        self.assertEqual(self.path.read_text(), original)

    def test_plaintext_reader_rejects_broad_permissions(self):
        """Refuse a credential file readable by another local account."""

        self.path.parent.mkdir()
        self.path.write_text(json.dumps({'version': 1, 'tokens': {}}))
        self.path.chmod(0o644)

        with self.assertRaisesRegex(CredentialError, 'broad permissions'):
            self.store.get(host='https://data.example.test', token_id='token-id')

    def test_plaintext_reader_rejects_corruption_and_unknown_formats(self):
        """Reject malformed JSON and structurally incompatible payloads."""

        self.path.parent.mkdir()
        cases = ['not json', '[]', '{"version": 2, "tokens": {}}', '{"version": 1, "tokens": []}']
        for payload in cases:
            with self.subTest(payload=payload):
                self.path.write_text(payload)
                self.path.chmod(0o600)
                expected = 'Could not read' if payload == 'not json' else 'unsupported format'
                with self.assertRaisesRegex(CredentialError, expected):
                    self.store.get(host='https://data.example.test', token_id='token-id')

    def test_plaintext_writer_translates_atomic_replace_failure(self):
        """Report an atomic persistence failure and remove its temporary file."""

        with patch('niyan.credentials.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaisesRegex(CredentialError, 'Could not write'):
                self.store.set(host='https://data.example.test', token_id='token-id', token='secret')
        self.assertEqual(list(self.path.parent.glob(f'.{self.path.name}.*')), [])


class CredentialStoreSelectionTests(unittest.TestCase):
    """Verify persisted storage discriminators select exact adapters."""

    def test_known_storage_names_select_adapters(self):
        """Return secure and explicit-insecure adapters without fallback."""

        stores = CredentialStores(paths=SimpleNamespace(insecure_credentials=Path('/tmp/credentials.json')), keyring_module=Mock())

        self.assertIs(stores.get('keyring'), stores.keyring)
        self.assertIs(stores.get('file'), stores.file)
        with self.assertRaisesRegex(CredentialError, 'unsupported'):
            stores.get('unknown')


if __name__ == '__main__':
    unittest.main()
