import json
import os
import tempfile
from pathlib import Path

from niyan.errors import CredentialError


class KeyringCredentialStore:
    """Store access tokens through the operating-system credential service."""

    storage_name = 'keyring'

    def __init__(self, module=None):
        """Initialize with an optional keyring-compatible module.

        Parameters
        ----------
        module : object, optional
            Dependency override used by tests.
        """

        self.module = module

    def set(self, *, host, token_id, token):
        """Persist one token secret.

        Parameters
        ----------
        host : str
            Installation origin.
        token_id : str
            Non-secret server token UUID.
        token : str
            Complete access-token secret.
        """

        keyring = self._module()
        try:
            keyring.set_password(_service_name(host), token_id, token)
        except Exception as error:
            raise CredentialError('The operating-system credential store rejected the token. Use --insecure-storage only if you accept plaintext storage.') from error

    def get(self, *, host, token_id):
        """Retrieve one token secret or report a missing credential.

        Parameters
        ----------
        host : str
            Installation origin.
        token_id : str
            Non-secret server token UUID.

        Returns
        -------
        str
            Complete access-token secret.
        """

        keyring = self._module()
        try:
            token = keyring.get_password(_service_name(host), token_id)
        except Exception as error:
            raise CredentialError('The operating-system credential store is unavailable.') from error
        if token is None:
            raise CredentialError('The selected Niyān credential is missing. Log in again instead of falling back to a broader token.')
        return token

    def delete(self, *, host, token_id):
        """Remove one token secret when rollback or logout requires it.

        Parameters
        ----------
        host : str
            Installation origin.
        token_id : str
            Non-secret server token UUID.
        """

        keyring = self._module()
        try:
            keyring.delete_password(_service_name(host), token_id)
        except Exception:
            return

    def _module(self):
        """Import keyring lazily so insecure headless mode remains usable.

        Returns
        -------
        module
            Keyring-compatible credential service API.
        """

        if self.module is not None:
            return self.module
        try:
            import keyring
        except ImportError as error:
            raise CredentialError('Secure credential storage is unavailable. Install the complete Niyān CLI or explicitly use --insecure-storage.') from error
        return keyring


class InsecureFileCredentialStore:
    """Implement the explicit mode-0600 headless credential fallback."""

    storage_name = 'file'

    def __init__(self, path):
        """Initialize the fallback at one user data file.

        Parameters
        ----------
        path : pathlib.Path
            Credential file outside any repository checkout.
        """

        self.path = Path(path)

    def set(self, *, host, token_id, token):
        """Persist one token after explicit insecure-storage selection.

        Parameters
        ----------
        host : str
            Installation origin.
        token_id : str
            Non-secret server token UUID.
        token : str
            Complete access-token secret.
        """

        credentials = self._load()
        credentials[_credential_key(host, token_id)] = token
        self._save(credentials)

    def get(self, *, host, token_id):
        """Retrieve one plaintext fallback token.

        Parameters
        ----------
        host : str
            Installation origin.
        token_id : str
            Non-secret server token UUID.

        Returns
        -------
        str
            Complete access-token secret.
        """

        token = self._load().get(_credential_key(host, token_id))
        if token is None:
            raise CredentialError('The selected Niyān credential is missing. Log in again instead of falling back to a broader token.')
        return token

    def delete(self, *, host, token_id):
        """Remove one plaintext fallback token if present.

        Parameters
        ----------
        host : str
            Installation origin.
        token_id : str
            Non-secret server token UUID.
        """

        credentials = self._load()
        if credentials.pop(_credential_key(host, token_id), None) is not None:
            self._save(credentials)

    def _load(self):
        """Read and permission-check the fallback credential file.

        Returns
        -------
        dict[str, str]
            Token secrets keyed by installation and token UUID.
        """

        if not self.path.exists():
            return {}
        try:
            if self.path.stat().st_mode & 0o077:
                raise CredentialError(f'Refusing insecure credential file with broad permissions: {self.path}')
            payload = json.loads(self.path.read_text())
        except CredentialError:
            raise
        except (OSError, json.JSONDecodeError) as error:
            raise CredentialError(f'Could not read insecure credential file at {self.path}.') from error
        if not isinstance(payload, dict) or payload.get('version') != 1 or not isinstance(payload.get('tokens'), dict):
            raise CredentialError(f'Insecure credential file at {self.path} has an unsupported format.')
        return payload['tokens']

    def _save(self, credentials):
        """Atomically write fallback credentials with mode 0600.

        Parameters
        ----------
        credentials : dict[str, str]
            Complete plaintext credential mapping.
        """

        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self.path.parent, prefix=f'.{self.path.name}.', delete=False) as temporary:
                json.dump({'version': 1, 'tokens': credentials}, temporary, indent=2, sort_keys=True)
                temporary.write('\n')
                temporary_path = Path(temporary.name)
            temporary_path.chmod(0o600)
            os.replace(temporary_path, self.path)
            self.path.chmod(0o600)
        except OSError as error:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise CredentialError(f'Could not write insecure credential file at {self.path}.') from error


class CredentialStores:
    """Select secure or explicitly insecure storage from binding metadata."""

    def __init__(self, *, paths, keyring_module=None):
        """Initialize both supported credential storage adapters.

        Parameters
        ----------
        paths : niyan.config.AppPaths
            User-level CLI paths.
        keyring_module : object, optional
            Secure-store test double.
        """

        self.keyring = KeyringCredentialStore(module=keyring_module)
        self.file = InsecureFileCredentialStore(paths.insecure_credentials)

    def get(self, storage_name):
        """Return the adapter identified by a configuration binding.

        Parameters
        ----------
        storage_name : str
            Persisted storage discriminator.

        Returns
        -------
        KeyringCredentialStore or InsecureFileCredentialStore
            Selected credential storage adapter.
        """

        if storage_name == self.keyring.storage_name:
            return self.keyring
        if storage_name == self.file.storage_name:
            return self.file
        raise CredentialError('The configured Niyān credential storage method is unsupported.')


def _service_name(host):
    """Return a host-scoped operating-system credential service name."""

    return f'niyan:{host}'


def _credential_key(host, token_id):
    """Return a collision-resistant JSON object key for a token."""

    return f'{host}\n{token_id}'
