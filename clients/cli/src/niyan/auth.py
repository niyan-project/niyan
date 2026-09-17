import os
import platform
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from niyan.config import Configuration, CredentialBinding, find_local_config, normalize_dataset_path
from niyan.errors import ApiError, ConfigurationError, CredentialError
from niyan.http import ApiClient, normalize_host


@dataclass(frozen=True)
class SelectedCredential:
    """Pair an access-token secret with non-secret account metadata."""

    token: str
    username: str
    binding: CredentialBinding | None
    source: str


def resolve_host(explicit_host, *, paths, cwd=None, environment=None):
    """Resolve host argument, environment override, or configured default.

    Parameters
    ----------
    explicit_host : str or None
        Host supplied on the command line.
    paths : niyan.config.AppPaths
        User configuration locations.
    cwd : pathlib.Path, optional
        Working directory used to discover checkout-local configuration.
    environment : mapping, optional
        Environment override used by tests.

    Returns
    -------
    str
        Normalized installation origin.
    """

    values = environment if environment is not None else os.environ
    selected = explicit_host or values.get('NIYAN_HOST')
    if selected:
        return normalize_host(selected)

    local_path = find_local_config(Path(cwd or Path.cwd()), required=False)
    local_configuration = Configuration.load(local_path) if local_path is not None and local_path.exists() else Configuration()
    global_configuration = Configuration.load(paths.global_config)
    selected = local_configuration.default_host or global_configuration.default_host
    if not selected:
        raise ConfigurationError('No Niyān host is selected. Pass a hostname, set NIYAN_HOST, or log in first.')
    return normalize_host(selected)


def login(*, host, paths, stores, local=False, dataset_path=None, read_only=False, insecure_storage=False, no_browser=False, cwd=None, api_factory=ApiClient, sleep=time.sleep, browser_open=webbrowser.open, stderr=None):
    """Complete device authorization and persist one credential binding.

    Parameters
    ----------
    host : str
        Normalized installation origin.
    paths : niyan.config.AppPaths
        User configuration and data locations.
    stores : niyan.credentials.CredentialStores
        Available credential storage adapters.
    local : bool, optional
        Restrict binding selection to the current Git checkout.
    dataset_path : str, optional
        Single dataset boundary requested from the server.
    read_only : bool, optional
        Request read-only scopes.
    insecure_storage : bool, optional
        Explicitly select the mode-0600 plaintext fallback.
    no_browser : bool, optional
        Do not attempt to open the verification URL.
    cwd : pathlib.Path, optional
        Working directory used for local configuration.
    api_factory : callable, optional
        API client constructor.
    sleep : callable, optional
        Polling wait function.
    browser_open : callable, optional
        Browser launcher.
    stderr : file-like object, optional
        Human-facing progress destination.

    Returns
    -------
    CredentialBinding
        Saved non-secret binding.
    """

    output = stderr or sys.stderr
    working_directory = Path(cwd or Path.cwd())
    normalized_dataset_path = normalize_dataset_path(dataset_path) if dataset_path else None
    scopes = ['read_api', 'read_repository'] if read_only else ['api', 'write_repository']
    api = api_factory(host)
    _, started = api.start_device_authorization(name=platform.node() or 'Niyān CLI', scopes=scopes, dataset_path=normalized_dataset_path)

    print(f"Open {started['verification_uri']} and enter code {started['user_code']}.", file=output)
    if not no_browser:
        try:
            browser_open(started['verification_uri_complete'])
        except Exception:
            pass

    interval = max(1, int(started['interval']))
    deadline = time.monotonic() + int(started['expires_in'])
    while True:
        if time.monotonic() >= deadline:
            raise ApiError('The device authorization expired before approval.', code='expired_device_code')
        try:
            status, exchanged = api.exchange_device_code(started['device_code'])
        except ApiError as error:
            if error.code in ('slow_down', 'rate_limited'):
                sleep(interval)
                continue
            raise
        if status == 202:
            sleep(interval)
            continue
        raw_token = exchanged.get('token')
        if not isinstance(raw_token, str):
            raise ApiError('Niyān did not return an access token after approval.')
        break

    _, current = api_factory(host, token=raw_token).current_user()
    token_metadata = current.get('access_token')
    if not isinstance(token_metadata, dict) or not token_metadata.get('id') or not current.get('username'):
        raise ApiError('Niyān returned incomplete access-token metadata.')

    storage = stores.file if insecure_storage else stores.keyring
    if insecure_storage:
        print('warning: storing the access token in a plaintext user file protected only by filesystem permissions.', file=output)
    binding = CredentialBinding(
        token_id=str(token_metadata['id']),
        username=current['username'],
        storage=storage.storage_name,
        dataset_id=str(token_metadata['dataset_id']) if token_metadata.get('dataset_id') else None,
        dataset_path=normalized_dataset_path,
    )
    storage.set(host=host, token_id=binding.token_id, token=raw_token)

    config_path = find_local_config(working_directory, required=True) if local else paths.global_config
    configuration = Configuration.load(config_path)
    configuration.set_binding(host=host, binding=binding)
    configuration.default_host = host
    try:
        configuration.save(config_path)
    except Exception:
        storage.delete(host=host, token_id=binding.token_id)
        raise
    print(f"Authenticated as {binding.username} on {host}{' for this checkout' if local else ''}.", file=output)
    return binding


def select_credential(*, host, dataset_path, paths, stores, cwd=None, environment=None):
    """Select local, global, or explicit environment credentials.

    Parameters
    ----------
    host : str
        Normalized installation origin.
    dataset_path : str
        Dataset path targeted by the operation.
    paths : niyan.config.AppPaths
        User configuration and data locations.
    stores : niyan.credentials.CredentialStores
        Credential storage adapters.
    cwd : pathlib.Path, optional
        Working directory used to discover local configuration.
    environment : mapping, optional
        Environment override used by tests.

    Returns
    -------
    SelectedCredential
        Credential and selection provenance.
    """

    values = environment if environment is not None else os.environ
    environment_token = values.get('NIYAN_TOKEN')
    if environment_token:
        return SelectedCredential(token=environment_token, username='niyan', binding=None, source='environment')

    normalized_dataset_path = normalize_dataset_path(dataset_path)
    local_path = find_local_config(Path(cwd or Path.cwd()), required=False)
    local_configuration = Configuration.load(local_path) if local_path is not None and local_path.exists() else Configuration()
    global_configuration = Configuration.load(paths.global_config)

    # Resource-specific credentials take precedence over broad credentials, while local configuration wins within each specificity level.
    candidates = (
        ('local', local_configuration.select_binding(host=host, dataset_path=normalized_dataset_path, include_default=False)),
        ('user', global_configuration.select_binding(host=host, dataset_path=normalized_dataset_path, include_default=False)),
        ('local', local_configuration.select_binding(host=host)),
        ('user', global_configuration.select_binding(host=host)),
    )
    for source, binding in candidates:
        if binding is not None:
            token = stores.get(binding.storage).get(host=host, token_id=binding.token_id)
            return SelectedCredential(token=token, username=binding.username, binding=binding, source=source)
    raise CredentialError(f'No Niyān credential is configured for {host}. Run `niyan auth login {host}` first.')
