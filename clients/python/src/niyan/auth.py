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
        raw_token = _normalize_access_token(exchanged.get('token'))
        break

    authenticated_api = api_factory(host, token=raw_token)
    _, current = authenticated_api.current_user()
    binding = _binding_from_current_user(current, storage_name=_selected_storage(stores, insecure_storage).storage_name, expected_dataset_path=normalized_dataset_path)
    try:
        _persist_credential(host=host, raw_token=raw_token, binding=binding, paths=paths, stores=stores, local=local, cwd=working_directory)
    except Exception:
        try:
            authenticated_api.revoke_access_token(binding.token_id)
        except Exception:
            pass
        raise

    if insecure_storage:
        print('warning: storing the access token in a plaintext user file protected only by filesystem permissions.', file=output)
    print(f"Authenticated as {binding.username} on {host}{' for this checkout' if local else ''}.", file=output)
    return binding


def login_with_token(*, host, raw_token, paths, stores, local=False, dataset_path=None, insecure_storage=False, cwd=None, api_factory=ApiClient, stderr=None):
    """Inspect and persist one manually issued access token.

    Parameters
    ----------
    host : str
        Normalized installation origin.
    raw_token : str
        Complete token supplied through standard input.
    paths : niyan.config.AppPaths
        User configuration and data locations.
    stores : niyan.credentials.CredentialStores
        Available credential storage adapters.
    local : bool, optional
        Restrict binding selection to the current Git checkout.
    dataset_path : str, optional
        Expected dataset boundary for a dataset-scoped token.
    insecure_storage : bool, optional
        Explicitly select the mode-0600 plaintext fallback.
    cwd : pathlib.Path, optional
        Working directory used for local configuration.
    api_factory : callable, optional
        API client constructor.
    stderr : file-like object, optional
        Human-facing progress destination.

    Returns
    -------
    CredentialBinding
        Saved non-secret binding.
    """

    output = stderr or sys.stderr
    token = _normalize_access_token(raw_token)
    normalized_dataset_path = normalize_dataset_path(dataset_path) if dataset_path else None
    _, current = api_factory(host, token=token).current_user()
    binding = _binding_from_current_user(current, storage_name=_selected_storage(stores, insecure_storage).storage_name, expected_dataset_path=normalized_dataset_path)
    _persist_credential(host=host, raw_token=token, binding=binding, paths=paths, stores=stores, local=local, cwd=Path(cwd or Path.cwd()))

    if insecure_storage:
        print('warning: storing the access token in a plaintext user file protected only by filesystem permissions.', file=output)
    print(f"Authenticated as {binding.username} on {host}{' for this checkout' if local else ''}.", file=output)
    return binding


def authentication_status(*, host, paths, stores, dataset_path=None, cwd=None, environment=None, api_factory=ApiClient, stdout=None):
    """Inspect the selected credential locally and against the server.

    Parameters
    ----------
    host : str
        Normalized installation origin.
    paths : niyan.config.AppPaths
        User configuration and data locations.
    stores : niyan.credentials.CredentialStores
        Available credential storage adapters.
    dataset_path : str, optional
        Dataset used for exact binding selection.
    cwd : pathlib.Path, optional
        Working directory used for local configuration.
    environment : mapping, optional
        Environment override used by tests.
    api_factory : callable, optional
        API client constructor.
    stdout : file-like object, optional
        Human-readable status destination.

    Returns
    -------
    bool
        Whether the server accepts the selected credential.
    """

    output = stdout or sys.stdout
    selected = select_credential(host=host, dataset_path=dataset_path, paths=paths, stores=stores, cwd=cwd, environment=environment)
    try:
        _, current = api_factory(host, token=selected.token).current_user()
    except ApiError as error:
        if error.status != 401:
            raise
        _print_authentication_status(host=host, selected=selected, metadata=None, accepted=False, output=output)
        return False

    metadata = _access_token_metadata(current)
    if selected.binding is not None and str(metadata['id']) != selected.binding.token_id:
        raise CredentialError('The stored credential does not match its configured token identifier. Log in again or use `niyan auth logout --forget`.')
    verified = SelectedCredential(token=selected.token, username=current['username'], binding=selected.binding, source=selected.source)
    _print_authentication_status(host=host, selected=verified, metadata=metadata, accepted=bool(metadata.get('active', True)), output=output)
    return bool(metadata.get('active', True))


def logout(*, host, paths, stores, local=False, dataset_path=None, forget=False, cwd=None, api_factory=ApiClient, stderr=None):
    """Revoke or forget one configured credential and remove local state.

    Parameters
    ----------
    host : str
        Normalized installation origin.
    paths : niyan.config.AppPaths
        User configuration and data locations.
    stores : niyan.credentials.CredentialStores
        Available credential storage adapters.
    local : bool, optional
        Target the current checkout's configuration instead of user config.
    dataset_path : str, optional
        Exact dataset binding to remove instead of the user-level binding.
    forget : bool, optional
        Remove local state without contacting the server.
    cwd : pathlib.Path, optional
        Working directory used for local configuration.
    api_factory : callable, optional
        API client constructor.
    stderr : file-like object, optional
        Human-facing progress destination.

    Returns
    -------
    CredentialBinding
        Removed non-secret binding.
    """

    output = stderr or sys.stderr
    normalized_dataset_path = normalize_dataset_path(dataset_path) if dataset_path else None
    config_path = find_local_config(Path(cwd or Path.cwd()), required=True) if local else paths.global_config
    configuration = Configuration.load(config_path)
    binding = configuration.select_binding(host=host, dataset_path=normalized_dataset_path, include_default=normalized_dataset_path is None)
    if binding is None:
        location = 'checkout-local' if local else 'user-level'
        target = f' for {normalized_dataset_path}' if normalized_dataset_path else ''
        raise CredentialError(f'No {location} Niyān credential is configured for {host}{target}.')

    store = stores.get(binding.storage)
    if forget:
        print('warning: removing the local credential without revoking it on the server.', file=output)
    else:
        token = store.get(host=host, token_id=binding.token_id)
        try:
            api_factory(host, token=token).revoke_access_token(binding.token_id)
        except ApiError as error:
            raise ApiError(f'{error} The local credential was kept. Use --forget only when server revocation is impossible.', status=error.status, code=error.code) from error

    removed = configuration.remove_binding(host=host, dataset_path=normalized_dataset_path)
    if removed is None:
        raise CredentialError('The selected Niyān credential disappeared before it could be removed.')
    configuration.save(config_path)
    store.delete(host=host, token_id=binding.token_id)
    action = 'Forgot' if forget else 'Revoked'
    print(f"{action} the credential for {binding.username} on {host}{' in this checkout' if local else ''}.", file=output)
    return binding


def select_credential(*, host, dataset_path, dataset_id=None, paths, stores, cwd=None, environment=None):
    """Select local, global, or explicit environment credentials.

    Parameters
    ----------
    host : str
        Normalized installation origin.
    dataset_path : str or None
        Dataset path targeted by the operation.
    dataset_id : str, optional
        Immutable checkout identity used to find a renamed dataset binding.
    paths : niyan.config.AppPaths
        User configuration locations.
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
        return SelectedCredential(token=_normalize_access_token(environment_token), username='unknown', binding=None, source='environment')

    normalized_dataset_path = normalize_dataset_path(dataset_path) if dataset_path else None
    local_path = find_local_config(Path(cwd or Path.cwd()), required=False)
    local_configuration = Configuration.load(local_path) if local_path is not None and local_path.exists() else Configuration()
    global_configuration = Configuration.load(paths.global_config)

    candidates = []
    if normalized_dataset_path is not None:
        candidates.extend(
            (
                ('local', local_configuration.select_binding(host=host, dataset_path=normalized_dataset_path, dataset_id=dataset_id, include_default=False)),
                ('user', global_configuration.select_binding(host=host, dataset_path=normalized_dataset_path, dataset_id=dataset_id, include_default=False)),
            )
        )
    candidates.extend(
        (
            ('local', local_configuration.select_binding(host=host)),
            ('user', global_configuration.select_binding(host=host)),
        )
    )
    for source, binding in candidates:
        if binding is not None:
            token = stores.get(binding.storage).get(host=host, token_id=binding.token_id)
            return SelectedCredential(token=token, username=binding.username, binding=binding, source=source)
    raise CredentialError(f'No Niyān credential is configured for {host}. Run `niyan auth login {host}` first.')


def _selected_storage(stores, insecure_storage):
    """Return the explicitly selected credential storage adapter."""

    return stores.file if insecure_storage else stores.keyring


def _normalize_access_token(raw_token):
    """Validate one complete token without exposing it in diagnostics."""

    if not isinstance(raw_token, str):
        raise CredentialError('Niyān did not receive an access token.')
    token = raw_token.strip()
    if not token or not token.startswith('niyan_') or any(character.isspace() for character in token):
        raise CredentialError('The supplied Niyān access token is invalid.')
    return token


def _access_token_metadata(current_user):
    """Validate current-user payload and return its token metadata."""

    if not isinstance(current_user, dict) or not isinstance(current_user.get('username'), str):
        raise ApiError('Niyān returned incomplete account metadata.')
    metadata = current_user.get('access_token')
    if not isinstance(metadata, dict) or not metadata.get('id'):
        raise ApiError('Niyān did not identify the access token used for authentication.')
    return metadata


def _binding_from_current_user(current_user, *, storage_name, expected_dataset_path=None):
    """Build a non-secret binding from validated server metadata."""

    metadata = _access_token_metadata(current_user)
    resource_boundary = metadata.get('resource_boundary')
    dataset_id = str(metadata['dataset_id']) if metadata.get('dataset_id') else None
    server_dataset_path = normalize_dataset_path(metadata['dataset_path']) if metadata.get('dataset_path') else None
    scopes = metadata.get('scopes')
    expires_at = metadata.get('expires_at')
    if resource_boundary not in ('user', 'dataset') or not isinstance(scopes, list) or any(not isinstance(scope, str) for scope in scopes) or not isinstance(expires_at, str):
        raise ApiError('Niyān returned incomplete access-token metadata.')
    if resource_boundary == 'dataset' and (dataset_id is None or server_dataset_path is None):
        raise ApiError('Niyān returned an incomplete dataset token boundary.')
    if resource_boundary == 'user' and (dataset_id is not None or server_dataset_path is not None):
        raise ApiError('Niyān returned inconsistent user-token metadata.')
    if expected_dataset_path is not None and server_dataset_path != expected_dataset_path:
        raise CredentialError(f'The supplied token is not restricted to {expected_dataset_path}.')
    return CredentialBinding(
        token_id=str(metadata['id']),
        username=current_user['username'],
        storage=storage_name,
        dataset_id=dataset_id,
        dataset_path=server_dataset_path,
        resource_boundary=resource_boundary,
        scopes=tuple(scopes),
        expires_at=expires_at,
    )


def _persist_credential(*, host, raw_token, binding, paths, stores, local, cwd):
    """Atomically bind a stored secret to non-secret configuration."""

    config_path = find_local_config(cwd, required=True) if local else paths.global_config
    configuration = Configuration.load(config_path)
    previous_binding = configuration.select_binding(host=host, dataset_path=binding.dataset_path, include_default=binding.dataset_path is None)
    store = stores.get(binding.storage)
    same_secret = previous_binding is not None and previous_binding.token_id == binding.token_id and previous_binding.storage == binding.storage
    store.set(host=host, token_id=binding.token_id, token=raw_token)
    configuration.set_binding(host=host, binding=binding)
    configuration.default_host = host
    try:
        configuration.save(config_path)
    except Exception:
        if not same_secret:
            store.delete(host=host, token_id=binding.token_id)
        raise
    if previous_binding is not None and not same_secret:
        stores.get(previous_binding.storage).delete(host=host, token_id=previous_binding.token_id)


def _print_authentication_status(*, host, selected, metadata, accepted, output):
    """Render non-secret credential and server-validation details."""

    binding = selected.binding
    username = selected.username
    token_metadata = metadata or {}
    boundary = token_metadata.get('resource_boundary') or (binding.resource_boundary if binding is not None else 'unknown')
    scopes = token_metadata.get('scopes') or (binding.scopes if binding is not None else ())
    expires_at = token_metadata.get('expires_at') or (binding.expires_at if binding is not None else None)
    dataset_path = token_metadata.get('dataset_path') or (binding.dataset_path if binding is not None else None)
    storage = binding.storage if binding is not None else 'environment'
    print(f'Host: {host}', file=output)
    print(f'Account: {username}', file=output)
    print(f'Source: {selected.source}', file=output)
    print(f'Storage: {storage}', file=output)
    print(f'Boundary: {boundary}', file=output)
    if dataset_path:
        print(f'Dataset: {dataset_path}', file=output)
    print(f"Scopes: {', '.join(scopes) if scopes else 'unknown'}", file=output)
    print(f'Expires: {expires_at or "unknown"}', file=output)
    print(f'Status: {"accepted" if accepted else "rejected"}', file=output)
