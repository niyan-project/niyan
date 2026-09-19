import json
import sys
import webbrowser
from pathlib import Path
from urllib.parse import quote

from niyan.auth import select_credential
from niyan.config import normalize_dataset_path
from niyan.errors import ApiError, ConfigurationError
from niyan.git import clone_dataset, load_checkout_identity, update_checkout_path
from niyan.http import ApiClient


def create_remote_dataset(*, host, dataset_path, name, clone, paths, stores, full_history=False, cwd=None, api_factory=ApiClient, stdin=None, stdout=None, stderr=None):
    """Create an empty dataset and optionally clone it.

    Parameters
    ----------
    host : str
        Normalized installation origin.
    dataset_path : str or None
        Human-facing target path, or ``None`` for an interactive prompt.
    name : str or None
        Optional display name.
    clone : bool
        Clone the newly created repository after creation.
    paths : niyan.config.AppPaths
        CLI configuration and data paths.
    stores : niyan.credentials.CredentialStores
        Credential storage adapters.
    full_history : bool, optional
        Fetch complete Git history when cloning after creation.
    cwd : pathlib.Path, optional
        Working directory for credential selection and cloning.
    api_factory : callable, optional
        API client constructor.
    stdin : file-like object, optional
        Prompt input override.
    stdout : file-like object, optional
        Created dataset output.
    stderr : file-like object, optional
        Clone progress output.

    Returns
    -------
    dict
        Created dataset representation.
    """

    input_stream = stdin or sys.stdin
    output = stdout or sys.stdout
    selected_path = dataset_path
    if selected_path is None:
        _require_interactive(input_stream, 'A dataset path is required outside an interactive terminal.')
        namespace_path = _prompt(input_stream, 'Namespace: ')
        slug = _prompt(input_stream, 'Dataset slug: ')
        selected_path = f'{namespace_path}/{slug}'
    normalized_path = normalize_dataset_path(selected_path)
    namespace_path, slug = normalized_path.rsplit('/', 1)
    display_name = name
    if display_name is None and dataset_path is None:
        display_name = _prompt(input_stream, f'Display name [{slug}]: ') or slug
    display_name = display_name or slug

    working_directory = Path(cwd or Path.cwd())
    credential = select_credential(host=host, dataset_path=normalized_path, paths=paths, stores=stores, cwd=working_directory)
    api = api_factory(host, token=credential.token)
    _, namespace = api.resolve_namespace(namespace_path)
    _, dataset = api.create_dataset(namespace_id=namespace['id'], slug=slug, name=display_name)
    canonical_path = _dataset_path(dataset)
    print(f"Created {canonical_path} ({dataset['id']})", file=output)
    if clone:
        clone_dataset(host=host, dataset_path=canonical_path, destination=None, paths=paths, stores=stores, full_history=full_history, cwd=working_directory, api_factory=api_factory, stderr=stderr)
    return dataset


def list_remote_datasets(*, host, namespace_path, limit, paths, stores, cwd=None, api_factory=ApiClient, stdout=None):
    """List one bounded page of visible datasets."""

    output = stdout or sys.stdout
    working_directory = Path(cwd or Path.cwd())
    credential = select_credential(host=host, dataset_path=None, paths=paths, stores=stores, cwd=working_directory)
    api = api_factory(host, token=credential.token)
    namespace_id = None
    if namespace_path is not None:
        _, namespace = api.resolve_namespace(_normalize_namespace_path(namespace_path))
        namespace_id = namespace['id']
    _, page = api.list_datasets(namespace_id=namespace_id, limit=limit)
    for dataset in page['items']:
        print(f"{_dataset_path(dataset)}\t{dataset['role']}\t{dataset['name']}", file=output)
    return page


def view_remote_dataset(*, host, dataset_path, web, paths, stores, cwd=None, api_factory=ApiClient, stdin=None, stdout=None, browser_open=webbrowser.open):
    """Display dataset identity, access metadata, and an optional README."""

    input_stream = stdin or sys.stdin
    output = stdout or sys.stdout
    working_directory = Path(cwd or Path.cwd())
    api, dataset_id, checkout = _resolve_dataset_target(host=host, dataset_path=dataset_path, input_stream=input_stream, working_directory=working_directory, paths=paths, stores=stores, api_factory=api_factory)
    _, dataset = api.get_dataset(dataset_id)
    canonical_path = _dataset_path(dataset)
    if checkout is not None:
        update_checkout_path(working_directory, canonical_path)
    if web:
        browser_open(f"{host}/{quote(canonical_path, safe='/')}")
        return dataset

    print(f"Path: {canonical_path}", file=output)
    print(f"ID: {dataset['id']}", file=output)
    print(f"Namespace: {dataset['namespace_path']}", file=output)
    print(f"Default branch: {dataset['default_branch']}", file=output)
    print(f"Access: {dataset['role']}", file=output)
    try:
        _, readme = api.get_repository_readme(dataset['id'], revision=dataset['default_branch'])
    except ApiError as error:
        if error.status != 404 or error.code not in ('readme_not_found', 'revision_not_found'):
            raise
    else:
        print('\nREADME\n', file=output)
        print(readme['content'].rstrip(), file=output)
    return dataset


def edit_remote_dataset(*, host, dataset_path, slug, name, paths, stores, cwd=None, api_factory=ApiClient, stdin=None, stdout=None):
    """Change a dataset's mutable slug or display name."""

    input_stream = stdin or sys.stdin
    output = stdout or sys.stdout
    working_directory = Path(cwd or Path.cwd())
    api, dataset_id, checkout = _resolve_dataset_target(host=host, dataset_path=dataset_path, input_stream=input_stream, working_directory=working_directory, paths=paths, stores=stores, api_factory=api_factory)
    selected_slug = slug
    selected_name = name
    if selected_slug is None and selected_name is None:
        _require_interactive(input_stream, 'Pass --slug or --name outside an interactive terminal.')
        _, current = api.get_dataset(dataset_id)
        selected_slug = _prompt(input_stream, f"Slug [{current['slug']}]: ") or None
        selected_name = _prompt(input_stream, f"Display name [{current['name']}]: ") or None
        if selected_slug is None and selected_name is None:
            raise ConfigurationError('No dataset changes were supplied.')
    _, dataset = api.update_dataset(dataset_id, slug=selected_slug, name=selected_name)
    if checkout is not None:
        update_checkout_path(working_directory, _dataset_path(dataset))
    print(f"Updated {_dataset_path(dataset)} ({dataset['id']})", file=output)
    return dataset


def delete_remote_dataset(*, host, dataset_path, confirmation, paths, stores, cwd=None, api_factory=ApiClient, stdin=None, stdout=None):
    """Permanently delete a dataset after exact-path confirmation."""

    input_stream = stdin or sys.stdin
    output = stdout or sys.stdout
    working_directory = Path(cwd or Path.cwd())
    api, dataset_id, _ = _resolve_dataset_target(host=host, dataset_path=dataset_path, input_stream=input_stream, working_directory=working_directory, paths=paths, stores=stores, api_factory=api_factory)
    _, dataset = api.get_dataset(dataset_id)
    canonical_path = _dataset_path(dataset)
    supplied_confirmation = confirmation
    if supplied_confirmation is None:
        _require_interactive(input_stream, f'Non-interactive deletion requires --confirm {canonical_path}.')
        supplied_confirmation = _prompt(input_stream, f'Type {canonical_path} to permanently delete it: ')
    if supplied_confirmation != canonical_path:
        raise ConfigurationError(f'Deletion confirmation must exactly match {canonical_path}.')
    api.delete_dataset(dataset_id)
    print(f'Deleted {canonical_path}', file=output)


def list_dataset_access(*, host, dataset_path, paths, stores, json_output=False, cwd=None, api_factory=ApiClient, stdin=None, stdout=None):
    """List explicit grants without presenting inherited access as editable."""

    output = stdout or sys.stdout
    api, dataset_id, _ = _resolve_dataset_target(host=host, dataset_path=dataset_path, input_stream=stdin or sys.stdin, working_directory=Path(cwd or Path.cwd()), paths=paths, stores=stores, api_factory=api_factory)
    _, dataset = api.get_dataset(dataset_id)
    _, page = api.list_dataset_grants(dataset_id)
    result = {'dataset_id': dataset_id, 'dataset_path': _dataset_path(dataset), 'effective_role': dataset['role'], 'count': page['count'], 'items': page['items']}
    if json_output:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=output)
        return result
    print(f"Your effective role: {dataset['role']}", file=output)
    if not page['items']:
        print('No explicit grants. Namespace membership may still provide effective access.', file=output)
        return result
    print('TYPE\tPRINCIPAL\tEXPLICIT ROLE', file=output)
    for grant in page['items']:
        print(f"{grant['principal_type']}\t{grant['principal_label']}\t{grant['role']}", file=output)
    return result


def grant_dataset_access(*, host, dataset_path, principal_type, principal, role, paths, stores, json_output=False, cwd=None, api_factory=ApiClient, stdin=None, stdout=None):
    """Create an explicit grant for one human-facing principal."""

    output = stdout or sys.stdout
    api, dataset_id, _ = _resolve_dataset_target(host=host, dataset_path=dataset_path, input_stream=stdin or sys.stdin, working_directory=Path(cwd or Path.cwd()), paths=paths, stores=stores, api_factory=api_factory)
    arguments = {'username': principal} if principal_type == 'user' else {'group_path': _normalize_namespace_path(principal)}
    _, grant = api.create_dataset_grant(dataset_id, role=role, **arguments)
    _print_grant_result(grant, action='Granted', json_output=json_output, output=output)
    return grant


def edit_dataset_access(*, host, dataset_path, principal_type, principal, role, paths, stores, json_output=False, cwd=None, api_factory=ApiClient, stdin=None, stdout=None):
    """Change one explicit grant selected by exact human-facing identity."""

    output = stdout or sys.stdout
    api, dataset_id, _ = _resolve_dataset_target(host=host, dataset_path=dataset_path, input_stream=stdin or sys.stdin, working_directory=Path(cwd or Path.cwd()), paths=paths, stores=stores, api_factory=api_factory)
    grant = _find_explicit_grant(api=api, dataset_id=dataset_id, principal_type=principal_type, principal=principal)
    _, updated = api.update_dataset_grant(dataset_id, grant['id'], role=role)
    _print_grant_result(updated, action='Updated', json_output=json_output, output=output)
    return updated


def revoke_dataset_access(*, host, dataset_path, principal_type, principal, paths, stores, json_output=False, cwd=None, api_factory=ApiClient, stdin=None, stdout=None):
    """Revoke one explicit grant selected by exact human-facing identity."""

    output = stdout or sys.stdout
    api, dataset_id, _ = _resolve_dataset_target(host=host, dataset_path=dataset_path, input_stream=stdin or sys.stdin, working_directory=Path(cwd or Path.cwd()), paths=paths, stores=stores, api_factory=api_factory)
    grant = _find_explicit_grant(api=api, dataset_id=dataset_id, principal_type=principal_type, principal=principal)
    api.delete_dataset_grant(dataset_id, grant['id'])
    result = {'revoked': True, 'dataset_id': dataset_id, 'principal_type': grant['principal_type'], 'principal': grant['principal_label']}
    if json_output:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=output)
    else:
        print(f"Revoked {grant['principal_type']} {grant['principal_label']}", file=output)
    return result


def _resolve_dataset_target(*, host, dataset_path, input_stream, working_directory, paths, stores, api_factory):
    """Resolve explicit paths or immutable checkout identity for an API call."""

    checkout = None
    selected_path = dataset_path
    if selected_path is None:
        checkout = load_checkout_identity(working_directory)
        if checkout is not None:
            if checkout.host != host:
                raise ConfigurationError(f'This checkout belongs to {checkout.host}, not {host}.')
            credential = select_credential(host=host, dataset_path=checkout.dataset_path, dataset_id=checkout.dataset_id, paths=paths, stores=stores, cwd=working_directory)
            return api_factory(host, token=credential.token), checkout.dataset_id, checkout
        _require_interactive(input_stream, 'A dataset path is required outside a Niyān checkout or an interactive terminal.')
        selected_path = _prompt(input_stream, 'Dataset path: ')

    normalized_path = normalize_dataset_path(selected_path)
    credential = select_credential(host=host, dataset_path=normalized_path, paths=paths, stores=stores, cwd=working_directory)
    api = api_factory(host, token=credential.token)
    _, location = api.resolve_dataset(normalized_path)
    return api, location['id'], checkout


def _normalize_namespace_path(namespace_path):
    """Normalize a required root or nested namespace path."""

    normalized = namespace_path.strip().strip('/')
    if not normalized or any(not part or part in ('.', '..') for part in normalized.split('/')):
        raise ConfigurationError('Namespace paths must contain one or more valid path components.')
    return normalized


def _find_explicit_grant(*, api, dataset_id, principal_type, principal):
    """Select one exact explicit grant without confusing inherited access."""

    normalized_principal = _normalize_namespace_path(principal) if principal_type == 'group' else principal
    _, page = api.list_dataset_grants(dataset_id)
    matches = [grant for grant in page['items'] if grant['principal_type'] == principal_type and grant['principal_label'] == normalized_principal]
    if not matches:
        raise ApiError(f'No explicit {principal_type} grant exists for {normalized_principal}.', status=404, code='grant_not_found')
    if len(matches) != 1:
        raise ConfigurationError(f'Multiple explicit {principal_type} grants unexpectedly match {normalized_principal}.')
    return matches[0]


def _print_grant_result(grant, *, action, json_output, output):
    """Render one grant for people or automation."""

    if json_output:
        print(json.dumps(grant, ensure_ascii=False, sort_keys=True), file=output)
    else:
        print(f"{action} {grant['principal_type']} {grant['principal_label']} explicit {grant['role']} access", file=output)


def _dataset_path(dataset):
    """Build the current human-facing path from an API representation."""

    return f"{dataset['namespace_path']}/{dataset['slug']}"


def _require_interactive(stream, message):
    """Reject a prompt when standard input is not a terminal."""

    try:
        interactive = stream.isatty()
    except (AttributeError, OSError):
        interactive = False
    if not interactive:
        raise ConfigurationError(message)


def _prompt(stream, message):
    """Read one trimmed interactive value while keeping prompts off stdout."""

    print(message, end='', file=sys.stderr, flush=True)
    return stream.readline().strip()
