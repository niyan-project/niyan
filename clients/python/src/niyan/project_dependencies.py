import os
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse
from uuid import UUID

from niyan.auth import select_credential
from niyan.config import normalize_dataset_path
from niyan.errors import GitError
from niyan.git import _authenticated_git, _git_environment, _materialize_lfs, _require_dependency, _run_git, clone_dataset, configure_lfs_transfer, load_checkout_identity
from niyan.http import ApiClient
from niyan.working_copy import _lfs_storage_directory, _lfs_worktree_state, _list_lfs_paths


OBJECT_ID = re.compile(r'^[0-9a-f]{40}(?:[0-9a-f]{24})?$')


def add_dataset_dependency(*, host, dataset_path, destination=None, revision=None, paths, stores, cwd=None, environment=None, api_factory=ApiClient, clone_function=clone_dataset, stdout=None):
    """Add one standard Git submodule pinned to an exact dataset commit."""

    output = stdout or sys.stdout
    project = _project_root(cwd)
    _reject_dataset_checkout(project)
    normalized_dataset = normalize_dataset_path(dataset_path)
    selected_destination = _dependency_path(destination or normalized_dataset.rsplit('/', 1)[-1])
    checkout = project.joinpath(*PurePosixPath(selected_destination).parts)
    if checkout.exists() or checkout.is_symlink():
        raise GitError(f'Dependency destination {selected_destination!r} already exists.')

    credential = select_credential(host=host, dataset_path=normalized_dataset, paths=paths, stores=stores, cwd=project, environment=environment)
    api = api_factory(host, token=credential.token)
    _, repository = api.resolve_dataset(normalized_dataset)
    dataset_id = str(repository['id'])
    canonical_path = str(repository['path'])
    git_url = _canonical_git_url(host=host, dataset_id=dataset_id, value=repository.get('git_url'))
    if revision is None:
        _, dataset = api.get_dataset(dataset_id)
        selected_revision = str(dataset['default_branch'])
        moving_ref = selected_revision
    else:
        selected_revision = revision
        moving_ref = _classify_moving_ref(api=api, dataset_id=dataset_id, revision=revision)
    _, resolution = api.resolve_revision(dataset_id, revision=selected_revision)
    resolved_commit = str(resolution['resolved_commit'])

    clone_function(host=host, dataset_path=canonical_path, destination=selected_destination, paths=paths, stores=stores, metadata_only=True, cwd=project, environment=environment, api_factory=api_factory, stderr=output)
    recording_started = False
    try:
        _checkout_exact_commit(checkout=checkout, host=host, credential=credential, commit=resolved_commit, environment=environment)
        _materialize_dependency(checkout=checkout, host=host, credential=credential, environment=environment)
        recording_started = True
        _record_submodule(project=project, dependency_path=selected_destination, git_url=git_url, moving_ref=moving_ref)
    except Exception:
        if not recording_started:
            shutil.rmtree(checkout, ignore_errors=True)
        raise
    print(f'Added {canonical_path} at {resolved_commit} in {selected_destination}', file=output)
    return {'dataset_id': dataset_id, 'dataset_path': canonical_path, 'path': selected_destination, 'resolved_commit': resolved_commit, 'moving_ref': moving_ref}


def remove_dataset_dependency(*, dependency_path, cwd=None):
    """Remove and stage one exact standard submodule path."""

    project = _project_root(cwd)
    _reject_dataset_checkout(project)
    normalized_path = _dependency_path(dependency_path)
    dependencies = {item['path']: item for item in _list_dependencies(project)}
    if normalized_path not in dependencies:
        raise GitError(f'{normalized_path!r} is not a dataset dependency in this project.')
    _git(project, ['submodule', 'deinit', '--force', '--', normalized_path], operation='deinitialize the dataset dependency')
    _git(project, ['rm', '--force', '--', normalized_path], operation='remove the dataset dependency')


def update_dataset_dependencies(*, dependency_path=None, revision=None, paths, stores, cwd=None, environment=None, api_factory=ApiClient, stdout=None):
    """Advance selected dependencies to exact commits and stage gitlinks."""

    output = stdout or sys.stdout
    project = _project_root(cwd)
    _reject_dataset_checkout(project)
    dependencies = _list_dependencies(project)
    if dependency_path is not None:
        normalized_path = _dependency_path(dependency_path)
        dependencies = [item for item in dependencies if item['path'] == normalized_path]
        if not dependencies:
            raise GitError(f'{normalized_path!r} is not a dataset dependency in this project.')
    updated = []
    for dependency in dependencies:
        checkout = project.joinpath(*PurePosixPath(dependency['path']).parts)
        identity = load_checkout_identity(checkout, validate_remote=False)
        host, dataset_id = _identity_from_url(dependency['url'])
        dataset_path = identity.dataset_path if identity is not None else None
        selected_revision = revision or dependency.get('branch')
        if selected_revision is None:
            continue
        credential = select_credential(host=host, dataset_path=dataset_path, dataset_id=dataset_id, paths=paths, stores=stores, cwd=project, environment=environment)
        api = api_factory(host, token=credential.token)
        _ensure_dependency_checkout(project=project, checkout=checkout, dependency_path=dependency['path'], host=host, credential=credential, environment=environment)
        _, resolution = api.resolve_revision(dataset_id, revision=selected_revision)
        resolved_commit = str(resolution['resolved_commit'])
        moving_ref = _classify_moving_ref(api=api, dataset_id=dataset_id, revision=selected_revision) if revision is not None else dependency.get('branch')
        _checkout_exact_commit(checkout=checkout, host=host, credential=credential, commit=resolved_commit, environment=environment)
        _materialize_dependency(checkout=checkout, host=host, credential=credential, environment=environment)
        _set_dependency_branch(project=project, name=dependency['name'], moving_ref=moving_ref)
        _git(project, ['add', '--', '.gitmodules', dependency['path']], operation='stage the updated dataset dependency')
        print(f"Updated {dependency['path']} to {resolved_commit}", file=output)
        updated.append({'path': dependency['path'], 'resolved_commit': resolved_commit, 'moving_ref': moving_ref})
    return updated


def show_dataset_dependencies(*, cwd=None, stdout=None):
    """Show pinned identity, checkout state, and unavailable LFS content."""

    output = stdout or sys.stdout
    project = _project_root(cwd)
    _reject_dataset_checkout(project)
    rows = []
    print('PATH\tDATASET\tHOST\tID\tPINNED\tMOVING REF\tSTATE\tLFS UNAVAILABLE', file=output)
    for dependency in _list_dependencies(project):
        host, dataset_id = _identity_from_url(dependency['url'])
        checkout = project.joinpath(*PurePosixPath(dependency['path']).parts)
        identity = load_checkout_identity(checkout, validate_remote=False) if checkout.exists() else None
        dataset_path = identity.dataset_path if identity is not None else '(unavailable)'
        pinned = _indexed_gitlink(project, dependency['path'])
        state = _submodule_state(project, dependency['path'])
        unavailable = _unavailable_lfs_count(checkout) if state != 'uninitialized' else '-'
        row = {'path': dependency['path'], 'dataset_path': dataset_path, 'host': host, 'dataset_id': dataset_id, 'pinned_commit': pinned, 'moving_ref': dependency.get('branch'), 'state': state, 'lfs_unavailable': unavailable}
        rows.append(row)
        print(f"{row['path']}\t{row['dataset_path']}\t{row['host']}\t{row['dataset_id']}\t{row['pinned_commit']}\t{row['moving_ref'] or '-'}\t{row['state']}\t{row['lfs_unavailable']}", file=output)
    return rows


def _project_root(cwd):
    """Return the enclosing non-bare Git worktree root."""

    _require_dependency('git', 'Git is required to manage project dataset dependencies.')
    selected = Path(cwd or Path.cwd())
    return Path(_git_text(selected, ['rev-parse', '--show-toplevel'], operation='locate the parent Git project'))


def _reject_dataset_checkout(project):
    """Prevent one dataset repository from containing another dataset dependency."""

    if load_checkout_identity(project, validate_remote=False) is not None:
        raise GitError('Dataset dependencies must be added to a parent research project, not inside a Niyān dataset checkout.')


def _dependency_path(value):
    """Validate one project-relative dependency path."""

    path = PurePosixPath(str(value))
    if path.is_absolute() or str(path) in ('', '.') or any(part in ('', '.', '..') for part in path.parts):
        raise GitError('Dataset dependency paths must be non-empty project-relative paths without traversal.')
    return path.as_posix()


def _canonical_git_url(*, host, dataset_id, value):
    """Verify a credential-free canonical dataset repository URL."""

    if not isinstance(value, str):
        raise GitError('Niyān returned incomplete repository location metadata.')
    parsed = urlparse(value)
    expected = urlparse(host)
    try:
        normalized_id = str(UUID(dataset_id))
    except ValueError as error:
        raise GitError('Niyān returned an invalid dataset identity.') from error
    if parsed.username or parsed.password or (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc) or parsed.path != f'/git/{normalized_id}.git' or parsed.params or parsed.query or parsed.fragment:
        raise GitError('Niyān returned an unsafe Git repository URL.')
    return value


def _identity_from_url(value):
    """Recover host and immutable dataset UUID from a canonical submodule URL."""

    parsed = urlparse(value)
    match = re.fullmatch(r'/git/([0-9a-fA-F-]{36})\.git', parsed.path)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password or match is None or parsed.params or parsed.query or parsed.fragment:
        raise GitError('The project contains a submodule that is not a canonical Niyān dataset URL.')
    try:
        dataset_id = str(UUID(match.group(1)))
    except ValueError as error:
        raise GitError('The project contains an invalid Niyān dataset identity.') from error
    return f'{parsed.scheme}://{parsed.netloc}', dataset_id


def _classify_moving_ref(*, api, dataset_id, revision):
    """Return an exact branch name, or ``None`` for a tag or commit."""

    matches = []
    for kind in ('branches', 'tags'):
        offset = 0
        while True:
            _, page = api.list_repository_refs(dataset_id, kind=kind, limit=100, offset=offset)
            if any(item['name'] == revision for item in page['items']):
                matches.append(kind)
                break
            if page.get('next_offset') is None:
                break
            offset = int(page['next_offset'])
    if len(matches) > 1:
        raise GitError(f'Revision {revision!r} is ambiguous between a branch and tag.')
    return revision if matches == ['branches'] else None


def _checkout_exact_commit(*, checkout, host, credential, commit, environment):
    """Fetch and detach at one server-resolved commit without persisting credentials."""

    child_environment = _git_environment(environment)
    with _authenticated_git(host=host, credential=credential) as command_prefix:
        fetched = _run_git([*command_prefix, '-C', str(checkout), 'fetch', '--depth=1', 'origin', commit], cwd=checkout, environment=child_environment, failure='Git could not fetch the pinned dataset commit.', capture_output=True)
        if fetched.returncode != 0:
            raise GitError('Git could not fetch the pinned dataset commit.')
    result = _run_git(['git', '-C', str(checkout), 'checkout', '--detach', commit], cwd=checkout, environment=child_environment, failure='Git could not check out the pinned dataset commit.', capture_output=True)
    if result.returncode != 0:
        raise GitError('Git could not check out the pinned dataset commit.')


def _ensure_dependency_checkout(*, project, checkout, dependency_path, host, credential, environment):
    """Initialize an ordinary uninitialized submodule without persisting credentials."""

    if checkout.exists():
        probe = _git(checkout, ['rev-parse', '--show-toplevel'], operation='inspect the dataset dependency checkout', accepted_statuses=frozenset({0, 128}))
        if probe.returncode == 0 and Path(os.fsdecode(probe.stdout).strip()) == checkout.resolve():
            return
    child_environment = _git_environment(environment)
    with _authenticated_git(host=host, credential=credential) as command_prefix:
        result = _run_git([*command_prefix, '-C', str(project), 'submodule', 'update', '--init', '--depth=1', '--', dependency_path], cwd=project, environment=child_environment, failure='Git could not initialize the dataset dependency.', capture_output=True)
        if result.returncode != 0:
            raise GitError('Git could not initialize the dataset dependency.')


def _materialize_dependency(*, checkout, host, credential, environment):
    """Configure and materialize the dependency through the shared LFS workflow."""

    _require_dependency('git-lfs', 'Git LFS is required to materialize dataset dependencies.')
    child_environment = _git_environment(environment)
    configure_lfs_transfer(checkout, environment=child_environment, install_filters=True)
    with _authenticated_git(host=host, credential=credential) as command_prefix:
        _materialize_lfs(command_prefix=command_prefix, checkout=checkout, remote='origin', include=(), exclude=(), environment=child_environment)


def _record_submodule(*, project, dependency_path, git_url, moving_ref):
    """Write only standard submodule fields and stage the parent changes."""

    name = dependency_path
    modules = project / '.gitmodules'
    _git(project, ['config', '--file', str(modules), f'submodule.{name}.path', dependency_path], operation='record the dataset dependency path')
    _git(project, ['config', '--file', str(modules), f'submodule.{name}.url', git_url], operation='record the dataset dependency URL')
    _set_dependency_branch(project=project, name=name, moving_ref=moving_ref)
    _git(project, ['add', '--', '.gitmodules', dependency_path], operation='stage the dataset dependency')


def _set_dependency_branch(*, project, name, moving_ref):
    """Persist or remove Git's standard moving submodule branch field."""

    key = f'submodule.{name}.branch'
    modules = project / '.gitmodules'
    if moving_ref is None:
        _git(project, ['config', '--file', str(modules), '--unset-all', key], operation='clear the dataset dependency branch', accepted_statuses={0, 5})
    else:
        _git(project, ['config', '--file', str(modules), key, moving_ref], operation='record the dataset dependency branch')


def _list_dependencies(project):
    """Parse standard submodule path, URL, and optional branch fields."""

    modules = project / '.gitmodules'
    if not modules.exists():
        return []
    result = _git(project, ['config', '--file', str(modules), '--name-only', '--get-regexp', r'^submodule\..*\.path$'], operation='list dataset dependencies', accepted_statuses={0, 1})
    dependencies = []
    for encoded_key in result.stdout.splitlines():
        key = os.fsdecode(encoded_key)
        name = key[len('submodule.') : -len('.path')]
        path = _git_text(project, ['config', '--file', str(modules), '--get', key], operation='read a dataset dependency path')
        url = _git_text(project, ['config', '--file', str(modules), '--get', f'submodule.{name}.url'], operation='read a dataset dependency URL')
        branch_result = _git(project, ['config', '--file', str(modules), '--get', f'submodule.{name}.branch'], operation='read a dataset dependency branch', accepted_statuses={0, 1})
        branch = os.fsdecode(branch_result.stdout).strip() if branch_result.returncode == 0 else None
        dependencies.append({'name': name, 'path': path, 'url': url, 'branch': branch})
    return sorted(dependencies, key=lambda item: item['path'])


def _indexed_gitlink(project, path):
    """Return the exact commit recorded by the parent index."""

    line = _git_text(project, ['ls-files', '--stage', '--', path], operation='read the pinned dataset commit')
    fields = line.split()
    if len(fields) < 3 or fields[0] != '160000' or not OBJECT_ID.fullmatch(fields[1]):
        raise GitError(f'Dataset dependency {path!r} is not recorded as a Git submodule.')
    return fields[1]


def _submodule_state(project, path):
    """Map Git's standard submodule status marker to a stable label."""

    result = _git(project, ['submodule', 'status', '--', path], operation='inspect a dataset dependency', accepted_statuses={0, 1})
    line = os.fsdecode(result.stdout).strip()
    if not line:
        return 'uninitialized'
    return {'-': 'uninitialized', '+': 'out-of-sync', 'U': 'conflict'}.get(line[0], 'clean')


def _unavailable_lfs_count(checkout):
    """Count pointer paths whose content is absent from worktree and cache."""

    try:
        git_directory = Path(_git_text(checkout, ['rev-parse', '--absolute-git-dir'], operation='locate dependency Git storage'))
        storage = _lfs_storage_directory(checkout, git_directory)
        return sum(1 for path in _list_lfs_paths(checkout) if _lfs_worktree_state(root=checkout, path=path, storage=storage) in ('missing', 'content unavailable'))
    except GitError:
        return '-'


def _git_text(cwd, arguments, *, operation):
    """Return one decoded Git result."""

    return os.fsdecode(_git(cwd, arguments, operation=operation).stdout).strip()


def _git(cwd, arguments, *, operation, accepted_statuses=frozenset({0})):
    """Run one bounded shell-free Git command."""

    if shutil.which('git') is None:
        raise GitError('Git is required to manage project dataset dependencies.')
    try:
        result = subprocess.run(['git', '-C', str(cwd), *arguments], check=False, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        raise GitError(f'Git could not {operation}.') from error
    if result.returncode not in accepted_statuses:
        raise GitError(f'Git could not {operation}.')
    return result
