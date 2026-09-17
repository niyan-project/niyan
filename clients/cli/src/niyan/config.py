import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from niyan.errors import ConfigurationError


@dataclass(frozen=True)
class AppPaths:
    """Locate user and checkout-private Niyān state."""

    config_home: Path
    data_home: Path

    @classmethod
    def from_environment(cls, environment=None):
        """Build platform paths from XDG-compatible environment variables.

        Parameters
        ----------
        environment : mapping, optional
            Environment override used by tests.

        Returns
        -------
        AppPaths
            Resolved configuration and data roots.
        """

        values = environment if environment is not None else os.environ
        home = Path(values.get('HOME', Path.home()))
        config_home = Path(values.get('XDG_CONFIG_HOME', home / '.config')) / 'niyan'
        data_home = Path(values.get('XDG_DATA_HOME', home / '.local' / 'share')) / 'niyan'
        return cls(config_home=config_home, data_home=data_home)

    @property
    def global_config(self):
        """Return the user-level non-secret configuration file.

        Returns
        -------
        pathlib.Path
            Global CLI configuration path.
        """

        return self.config_home / 'config.json'

    @property
    def insecure_credentials(self):
        """Return the explicitly insecure credential file path.

        Returns
        -------
        pathlib.Path
            User data path for the opt-in plaintext credential store.
        """

        return self.data_home / 'credentials.json'


@dataclass(frozen=True)
class CredentialBinding:
    """Reference a secret without storing it in CLI configuration."""

    token_id: str
    username: str
    storage: str
    dataset_id: str | None = None
    dataset_path: str | None = None

    @classmethod
    def from_dict(cls, value):
        """Validate and deserialize one credential binding.

        Parameters
        ----------
        value : dict
            JSON-decoded binding.

        Returns
        -------
        CredentialBinding
            Validated binding.

        Raises
        ------
        ConfigurationError
            If required fields or storage metadata are invalid.
        """

        if not isinstance(value, dict) or not isinstance(value.get('token_id'), str) or not isinstance(value.get('username'), str):
            raise ConfigurationError('Niyān credential configuration is invalid.')
        storage = value.get('storage')
        if storage not in ('keyring', 'file'):
            raise ConfigurationError('Niyān credential storage configuration is invalid.')
        return cls(
            token_id=value['token_id'],
            username=value['username'],
            storage=storage,
            dataset_id=value.get('dataset_id'),
            dataset_path=value.get('dataset_path'),
        )

    def to_dict(self):
        """Return a JSON-safe non-secret representation.

        Returns
        -------
        dict
            Binding fields suitable for CLI configuration.
        """

        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass
class Configuration:
    """Store host and token bindings while excluding credential secrets."""

    default_host: str | None = None
    hosts: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path):
        """Load a configuration file or return an empty configuration.

        Parameters
        ----------
        path : pathlib.Path
            File to read.

        Returns
        -------
        Configuration
            Parsed configuration.
        """

        if not path.exists():
            return cls()
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise ConfigurationError(f'Could not read Niyān configuration at {path}.') from error
        if not isinstance(payload, dict) or payload.get('version') != 1 or not isinstance(payload.get('hosts', {}), dict):
            raise ConfigurationError(f'Niyān configuration at {path} has an unsupported format.')
        return cls(default_host=payload.get('default_host'), hosts=payload.get('hosts', {}))

    def save(self, path):
        """Atomically persist non-secret configuration with private permissions.

        Parameters
        ----------
        path : pathlib.Path
            Destination configuration file.
        """

        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {'version': 1, 'default_host': self.default_host, 'hosts': self.hosts}
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, prefix=f'.{path.name}.', delete=False) as temporary:
                json.dump(payload, temporary, indent=2, sort_keys=True)
                temporary.write('\n')
                temporary_path = Path(temporary.name)
            temporary_path.chmod(0o600)
            os.replace(temporary_path, path)
        except OSError as error:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise ConfigurationError(f'Could not write Niyān configuration at {path}.') from error

    def set_binding(self, *, host, binding):
        """Set a host default or exact dataset credential binding.

        Parameters
        ----------
        host : str
            Normalized installation origin.
        binding : CredentialBinding
            Non-secret credential reference.
        """

        host_config = self.hosts.setdefault(host, {'datasets': {}})
        host_config.setdefault('datasets', {})
        if binding.dataset_path:
            host_config['datasets'][binding.dataset_path] = binding.to_dict()
        else:
            host_config['default'] = binding.to_dict()

    def select_binding(self, *, host, dataset_path=None, include_default=True):
        """Select an exact dataset binding before the host default.

        Parameters
        ----------
        host : str
            Normalized installation origin.
        dataset_path : str, optional
            Human-facing dataset path.
        include_default : bool, optional
            Return the host default when no exact dataset binding exists.

        Returns
        -------
        CredentialBinding or None
            Selected binding when configured.
        """

        host_config = self.hosts.get(host)
        if not isinstance(host_config, dict):
            return None
        if dataset_path:
            encoded_binding = host_config.get('datasets', {}).get(dataset_path)
            if encoded_binding is not None:
                return CredentialBinding.from_dict(encoded_binding)
        if not include_default:
            return None
        encoded_binding = host_config.get('default')
        return CredentialBinding.from_dict(encoded_binding) if encoded_binding is not None else None


def find_local_config(cwd, *, required=False):
    """Return checkout-private config beneath the current Git directory.

    Parameters
    ----------
    cwd : pathlib.Path or str
        Working directory selected by the caller.
    required : bool, optional
        Raise when the directory is not inside a Git working tree.

    Returns
    -------
    pathlib.Path or None
        Checkout-private configuration path.
    """

    try:
        result = subprocess.run(['git', '-C', str(cwd), 'rev-parse', '--absolute-git-dir'], check=False, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        if required:
            raise ConfigurationError('Git is required for checkout-local Niyān configuration.') from error
        return None
    if result.returncode != 0:
        if required:
            raise ConfigurationError('--local requires the current directory to be inside a Git working tree.')
        return None
    return Path(result.stdout.strip()) / 'niyan' / 'config.json'


def normalize_dataset_path(dataset_path):
    """Normalize a required ``namespace/dataset`` path."""

    normalized = dataset_path.strip().strip('/')
    if len(normalized.split('/')) < 2 or any(not part or part in ('.', '..') for part in normalized.split('/')):
        raise ConfigurationError('Dataset paths must contain a namespace and dataset slug.')
    return normalized
