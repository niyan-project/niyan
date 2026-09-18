import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

from niyan.auth import select_credential
from niyan.config import CheckoutIdentity, Configuration, find_local_config
from niyan.errors import CredentialError, GitConflictError, GitDependencyError, GitError
from niyan.http import ApiClient


def clone_dataset(*, host, dataset_path, destination, paths, stores, full_history=False, include=None, exclude=None, metadata_only=False, cwd=None, environment=None, api_factory=ApiClient, stderr=None):
    """Resolve and clone one dataset through Niyān-managed Git credentials.

    Parameters
    ----------
    host : str
        Normalized installation origin.
    dataset_path : str
        Human-facing dataset path.
    destination : str or None
        Optional checkout destination.
    paths : niyan.config.AppPaths
        CLI configuration and data paths.
    stores : niyan.credentials.CredentialStores
        Credential storage adapters.
    full_history : bool, optional
        Clone all reachable history and remote branches instead of the shallow default.
    include : list[str], optional
        Repeatable Git LFS include patterns saved for later pulls.
    exclude : list[str], optional
        Repeatable Git LFS exclude patterns saved for later pulls.
    metadata_only : bool, optional
        Leave LFS pointer files unmaterialized for this operation.
    cwd : pathlib.Path, optional
        Working directory for local credential selection and Git.
    environment : mapping, optional
        Parent environment override used by tests.
    api_factory : callable, optional
        API client constructor.
    stderr : file-like object, optional
        Human-facing progress destination.

    Returns
    -------
    pathlib.Path
        Destination of the completed clone.
    """

    output = stderr or sys.stderr
    working_directory = Path(cwd or Path.cwd())
    credential = select_credential(host=host, dataset_path=dataset_path, paths=paths, stores=stores, cwd=working_directory, environment=environment)
    _, repository = api_factory(host, token=credential.token).resolve_dataset(dataset_path)
    dataset_id = str(repository.get('id', ''))
    git_url = repository.get('git_url')
    canonical_path = repository.get('path')
    if not dataset_id or not isinstance(git_url, str) or not isinstance(canonical_path, str):
        raise GitError('Niyān returned incomplete repository location metadata.')
    parsed_git_url = urlparse(git_url)
    if _origin(git_url) != _origin(host) or parsed_git_url.path != f'/git/{dataset_id}.git' or parsed_git_url.params or parsed_git_url.query or parsed_git_url.fragment:
        raise GitError('Niyān returned an unsafe Git repository URL.')

    destination_path = Path(destination) if destination else Path(canonical_path.rsplit('/', 1)[-1])
    if not destination_path.is_absolute():
        destination_path = working_directory / destination_path
    _require_dependency('git', 'Git is required to clone Niyān datasets.')
    lfs_include = _materialization_patterns(include)
    lfs_exclude = _materialization_patterns(exclude)
    if not metadata_only:
        _require_dependency('git-lfs', 'Git LFS is required to materialize dataset files. Use --metadata-only to clone pointer files without it.')

    child_environment = dict(environment if environment is not None else os.environ)
    child_environment.pop('NIYAN_TOKEN', None)
    # Checkout pointers first so all bulk materialization happens through one explicit, filtered Git LFS operation.
    child_environment['GIT_LFS_SKIP_SMUDGE'] = '1'
    with _authenticated_git(host=host, credential=credential) as command_prefix:
        command = [*command_prefix, 'clone']
        if not full_history:
            command.extend(['--depth=1', '--single-branch', '--no-tags'])
        command.extend(['--', git_url, str(destination_path)])
        result = _run_git(command, cwd=working_directory, environment=child_environment, failure='Git could not clone the requested dataset.')
        if result.returncode != 0:
            raise GitError('Git could not clone the requested dataset.')
        _save_checkout_identity(
            destination_path,
            CheckoutIdentity(
                host=host,
                dataset_id=dataset_id,
                dataset_path=canonical_path,
                history='full' if full_history else 'shallow',
                lfs_include=lfs_include,
                lfs_exclude=lfs_exclude,
            ),
        )
        configure_lfs_transfer(destination_path, environment=child_environment, install_filters=not metadata_only)
        head = _run_git(
            ['git', '-C', str(destination_path), 'rev-parse', '--verify', 'HEAD'],
            cwd=destination_path,
            environment=child_environment,
            failure='Git could not inspect the cloned dataset state.',
            capture_output=True,
        )
        if head.returncode not in (0, 128):
            raise GitError('Git could not inspect the cloned dataset state.')
        if not metadata_only and head.returncode == 0:
            _materialize_lfs(command_prefix=command_prefix, checkout=destination_path, remote='origin', include=lfs_include, exclude=lfs_exclude, environment=child_environment)
    if metadata_only:
        print('Clone complete. Git LFS content was left as pointer files.', file=output)
    elif head.returncode == 128:
        print('Clone complete. The dataset has no commits yet.', file=output)
    else:
        print('Clone complete.', file=output)
    return destination_path


def configure_lfs_transfer(checkout, *, environment=None, install_filters=False):
    """Configure Niyān's upload-only custom transfer in one managed checkout.

    Parameters
    ----------
    checkout : pathlib.Path
        Niyān dataset working tree.
    environment : mapping, optional
        Child process environment override used by tests.
    install_filters : bool, optional
        Ask stock Git LFS to install checkout-local clean and smudge filters.
    """

    child_environment = _git_environment(environment)
    if install_filters:
        installed = _run_git(
            ['git', '-C', str(checkout), 'lfs', 'install', '--local', '--skip-smudge'],
            cwd=checkout,
            environment=child_environment,
            failure='Git LFS could not configure checkout-local filters.',
        )
        if installed.returncode != 0:
            raise GitError('Git LFS could not configure checkout-local filters.')
    settings = (
        ('lfs.customtransfer.niyan-multipart.path', 'niyan'),
        ('lfs.customtransfer.niyan-multipart.args', '_lfs-transfer'),
        ('lfs.customtransfer.niyan-multipart.concurrent', 'false'),
        ('lfs.customtransfer.niyan-multipart.direction', 'upload'),
    )
    for name, value in settings:
        result = _run_git(['git', '-C', str(checkout), 'config', '--local', '--replace-all', name, value], cwd=checkout, environment=child_environment, failure='Git could not configure the Niyān LFS transfer agent.')
        if result.returncode != 0:
            raise GitError('Git could not configure the Niyān LFS transfer agent.')


def fetch_dataset(*, paths, stores, cwd=None, environment=None, stderr=None):
    """Fetch remote Git refs without changing the current worktree or LFS cache.

    Parameters
    ----------
    paths : niyan.config.AppPaths
        CLI configuration and data paths.
    stores : niyan.credentials.CredentialStores
        Credential storage adapters.
    cwd : pathlib.Path, optional
        Directory inside the Niyān checkout.
    environment : mapping, optional
        Parent environment override used by tests.
    stderr : file-like object, optional
        Human-facing progress destination.
    """

    checkout = Path(cwd or Path.cwd())
    identity = _require_checkout(checkout)
    credential = select_credential(host=identity.host, dataset_path=identity.dataset_path, dataset_id=identity.dataset_id, paths=paths, stores=stores, cwd=checkout, environment=environment)
    child_environment = _git_environment(environment)
    with _authenticated_git(host=identity.host, credential=credential) as command_prefix:
        result = _run_git([*command_prefix, '-C', str(checkout), 'fetch', identity.remote], cwd=checkout, environment=child_environment, failure='Git could not fetch the dataset remote.')
        if result.returncode != 0:
            raise GitError('Git could not fetch the dataset remote.')
    print('Fetch complete.', file=stderr or sys.stderr)


def pull_dataset(*, paths, stores, full_history=False, include=None, exclude=None, metadata_only=False, cwd=None, environment=None, stderr=None):
    """Fast-forward one checkout and materialize its selected LFS working set.

    Parameters
    ----------
    paths : niyan.config.AppPaths
        CLI configuration and data paths.
    stores : niyan.credentials.CredentialStores
        Credential storage adapters.
    full_history : bool, optional
        Convert a shallow checkout to complete Git history.
    include : list[str], optional
        Replacement repeatable Git LFS include patterns.
    exclude : list[str], optional
        Replacement repeatable Git LFS exclude patterns.
    metadata_only : bool, optional
        Skip LFS materialization for this invocation.
    cwd : pathlib.Path, optional
        Directory inside the Niyān checkout.
    environment : mapping, optional
        Parent environment override used by tests.
    stderr : file-like object, optional
        Human-facing progress destination.
    """

    checkout = Path(cwd or Path.cwd())
    identity = _require_checkout(checkout)
    selected_include = identity.lfs_include if include is None else _materialization_patterns(include)
    selected_exclude = identity.lfs_exclude if exclude is None else _materialization_patterns(exclude)
    if not metadata_only:
        _require_dependency('git-lfs', 'Git LFS is required to materialize dataset files. Use --metadata-only to update pointer files without it.')
    credential = select_credential(host=identity.host, dataset_path=identity.dataset_path, dataset_id=identity.dataset_id, paths=paths, stores=stores, cwd=checkout, environment=environment)
    child_environment = _git_environment(environment)
    with _authenticated_git(host=identity.host, credential=credential) as command_prefix:
        branch = _current_branch(checkout, child_environment)
        upstream = _current_upstream(checkout, child_environment)
        if not upstream.startswith(f'{identity.remote}/'):
            raise GitError(f'The current branch does not track the configured Niyān remote {identity.remote!r}.')

        if full_history and identity.history == 'shallow':
            fetch_arguments = ['fetch', '--unshallow', '--tags', identity.remote]
        elif full_history:
            fetch_arguments = ['fetch', '--tags', identity.remote]
        else:
            fetch_arguments = ['fetch', identity.remote]
        fetched = _run_git([*command_prefix, '-C', str(checkout), *fetch_arguments], cwd=checkout, environment=child_environment, failure='Git could not fetch the dataset remote.')
        if fetched.returncode != 0:
            raise GitError('Git could not fetch the dataset remote.')

        merged = _run_git([*command_prefix, '-C', str(checkout), 'merge', '--ff-only', '--no-edit', upstream], cwd=checkout, environment=child_environment, failure='Git could not update the current dataset branch.', capture_output=True)
        if merged.returncode != 0:
            raise GitConflictError('The current dataset branch has diverged or contains changes that prevent a fast-forward pull. Resolve it explicitly with Niyān merge commands.')

        updated_identity = replace(identity, history='full' if full_history else identity.history, lfs_include=selected_include, lfs_exclude=selected_exclude)
        if updated_identity.history == 'shallow':
            reshallowed = _run_git(
                [*command_prefix, '-C', str(checkout), 'fetch', '--depth=1', '--no-tags', identity.remote, branch],
                cwd=checkout,
                environment=child_environment,
                failure='Git updated the dataset but could not restore the depth-one history policy.',
            )
            if reshallowed.returncode != 0:
                raise GitError('Git updated the dataset but could not restore the depth-one history policy.')
        _save_checkout_identity(checkout, updated_identity)
        if not metadata_only:
            _materialize_lfs(command_prefix=command_prefix, checkout=checkout, remote=identity.remote, include=selected_include, exclude=selected_exclude, environment=child_environment)

    if metadata_only:
        print('Pull complete. Git LFS content was left as pointer files.', file=stderr or sys.stderr)
    else:
        print('Pull complete.', file=stderr or sys.stderr)


def push_dataset(*, paths, stores, cwd=None, environment=None, stderr=None):
    """Upload reachable LFS objects before publishing the current branch.

    Parameters
    ----------
    paths : niyan.config.AppPaths
        CLI configuration and data paths.
    stores : niyan.credentials.CredentialStores
        Credential storage adapters.
    cwd : pathlib.Path, optional
        Directory inside the Niyān checkout.
    environment : mapping, optional
        Parent environment override used by tests.
    stderr : file-like object, optional
        Human-facing progress destination.

    Raises
    ------
    GitConflictError
        If the remote rejects the ref update, including a non-fast-forward push.
    GitError
        If LFS publication fails or the push is interrupted.
    """

    checkout = Path(cwd or Path.cwd())
    identity = _require_checkout(checkout)
    _require_dependency('git-lfs', 'Git LFS is required to publish Niyān datasets.')
    credential = select_credential(host=identity.host, dataset_path=identity.dataset_path, dataset_id=identity.dataset_id, paths=paths, stores=stores, cwd=checkout, environment=environment)
    child_environment = _git_environment(environment)
    branch = _current_branch(checkout, child_environment, operation='push')
    _git_output(checkout, ['rev-parse', '--verify', 'HEAD'], child_environment, 'The current dataset branch has no commits to push.')
    upstream = _optional_current_upstream(checkout, branch, child_environment)
    if upstream is not None and not upstream.startswith(f'{identity.remote}/'):
        raise GitError(f'The current branch does not track the configured Niyān remote {identity.remote!r}.')
    remote_branch = upstream.removeprefix(f'{identity.remote}/') if upstream is not None else branch

    configure_lfs_transfer(checkout, environment=child_environment, install_filters=True)
    with _authenticated_git(host=identity.host, credential=credential) as command_prefix:
        try:
            uploaded = _run_git(
                [*command_prefix, '-C', str(checkout), 'lfs', 'push', identity.remote, branch],
                cwd=checkout,
                environment=child_environment,
                failure='Git LFS could not publish the dataset files.',
                capture_output=True,
            )
            if uploaded.returncode != 0:
                raise GitError('Git LFS could not publish every required dataset file. The Git branch was not updated.')

            # LFS was deliberately completed above. Suppress the hook's duplicate scan while preserving normal Git push behavior.
            push_environment = dict(child_environment)
            push_environment['GIT_LFS_SKIP_PUSH'] = '1'
            push_arguments = ['push']
            if upstream is None:
                push_arguments.append('--set-upstream')
            push_arguments.extend([identity.remote, f'{branch}:refs/heads/{remote_branch}'])
            pushed = _run_git(
                [*command_prefix, '-C', str(checkout), *push_arguments],
                cwd=checkout,
                environment=push_environment,
                failure='Git could not publish the dataset branch.',
                capture_output=True,
            )
        except KeyboardInterrupt as error:
            raise GitError('Dataset publication was interrupted. The Git branch was not reported as published.') from error

    if pushed.returncode != 0:
        raise GitConflictError('The remote rejected the dataset branch update. Pull the latest changes and resolve any divergence before trying again.')
    print('Push complete.', file=stderr or sys.stderr)


def load_checkout_identity(cwd, *, validate_remote=True, run=subprocess.run):
    """Load checkout identity and optionally verify its configured Git remote.

    Parameters
    ----------
    cwd : pathlib.Path or str
        Directory inside the candidate dataset checkout.
    validate_remote : bool, optional
        Compare the configured remote to the immutable dataset identity.
    run : callable, optional
        Subprocess runner override used by tests.

    Returns
    -------
    niyan.config.CheckoutIdentity or None
        Checkout identity, or ``None`` outside a configured Niyān checkout.

    Raises
    ------
    GitError
        If a configured checkout no longer points at its expected repository.
    """

    config_path = find_local_config(Path(cwd), required=False)
    if config_path is None or not config_path.exists():
        return None
    identity = Configuration.load(config_path).checkout
    if identity is None:
        return None
    if validate_remote:
        _validate_checkout_remote(Path(cwd), identity, run=run)
    return identity


def update_checkout_path(cwd, dataset_path):
    """Refresh the mutable path stored for an immutable checkout identity.

    Parameters
    ----------
    cwd : pathlib.Path or str
        Directory inside the dataset checkout.
    dataset_path : str
        Current canonical path returned by the server.
    """

    config_path = find_local_config(Path(cwd), required=False)
    if config_path is None or not config_path.exists():
        return
    configuration = Configuration.load(config_path)
    identity = configuration.checkout
    if identity is None or identity.dataset_path == dataset_path:
        return
    configuration.set_checkout(replace(identity, dataset_path=dataset_path))
    configuration.save(config_path)


def _require_checkout(checkout):
    """Load and validate the current checkout or raise an actionable error."""

    _require_dependency('git', 'Git is required to synchronize Niyān datasets.')
    identity = load_checkout_identity(checkout)
    if identity is None:
        raise GitError('This command must run inside a configured Niyān dataset checkout.')
    return identity


def _require_dependency(executable, message):
    """Require one external repository tool before mutating local state."""

    if shutil.which(executable) is None:
        raise GitDependencyError(message)


def _materialization_patterns(patterns):
    """Validate repeatable patterns before passing one joined value to Git LFS."""

    normalized = tuple(patterns or ())
    if any(not isinstance(pattern, str) or not pattern or '\x00' in pattern or ',' in pattern for pattern in normalized):
        raise GitError('Git LFS include and exclude patterns must be non-empty globs without NUL bytes or commas.')
    return normalized


def _git_environment(environment):
    """Build a minimal child override that suppresses implicit LFS smudging."""

    child_environment = dict(environment if environment is not None else os.environ)
    child_environment.pop('NIYAN_TOKEN', None)
    child_environment['GIT_LFS_SKIP_SMUDGE'] = '1'
    return child_environment


@contextmanager
def _authenticated_git(*, host, credential):
    """Yield Git arguments for one credential and remove any secret handoff."""

    helper_arguments, temporary_secret = _credential_helper_arguments(host=host, credential=credential)
    helper_config = f'!{shlex.join(helper_arguments)}'
    try:
        yield [
            'git',
            '-c',
            'credential.helper=',
            '-c',
            f'credential.helper={helper_config}',
            '-c',
            'credential.useHttpPath=true',
        ]
    finally:
        if temporary_secret is not None:
            temporary_secret.unlink(missing_ok=True)


def _run_git(command, *, cwd, environment, failure, capture_output=False):
    """Run one shell-free Git command while translating process-start failures."""

    try:
        return subprocess.run(command, cwd=cwd, env=environment, check=False, capture_output=capture_output)
    except OSError as error:
        raise GitError(failure) from error


def _git_output(checkout, arguments, environment, failure):
    """Read one bounded local Git value without exposing raw diagnostics."""

    try:
        result = subprocess.run(['git', '-C', str(checkout), *arguments], cwd=checkout, env=environment, check=False, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        raise GitError(failure) from error
    if result.returncode != 0:
        raise GitError(failure)
    return result.stdout.strip()


def _current_branch(checkout, environment, *, operation='pull'):
    """Return the attached current branch or reject detached HEAD."""

    branch = _git_output(checkout, ['symbolic-ref', '--quiet', '--short', 'HEAD'], environment, f'Niyān {operation} requires an attached current branch.')
    if not branch:
        raise GitError(f'Niyān {operation} requires an attached current branch.')
    return branch


def _current_upstream(checkout, environment):
    """Return the current branch's configured upstream ref."""

    return _git_output(checkout, ['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}'], environment, 'The current dataset branch has no configured upstream.')


def _optional_current_upstream(checkout, branch, environment):
    """Return the current upstream ref, or ``None`` for a first branch push."""

    try:
        result = subprocess.run(
            ['git', '-C', str(checkout), 'for-each-ref', '--format=%(upstream:short)', f'refs/heads/{branch}'],
            cwd=checkout,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GitError('Git could not inspect the current branch upstream.') from error
    if result.returncode != 0:
        raise GitError('Git could not inspect the current branch upstream.')
    upstream = result.stdout.strip()
    return upstream or None


def _materialize_lfs(*, command_prefix, checkout, remote, include, exclude, environment):
    """Download and check out one explicitly selected Git LFS working set."""

    result = _run_git(
        [*command_prefix, '-C', str(checkout), 'lfs', 'pull', f'--include={",".join(include)}', f'--exclude={",".join(exclude)}', remote],
        cwd=checkout,
        environment=environment,
        failure='Git LFS could not materialize the selected dataset files.',
    )
    if result.returncode != 0:
        raise GitError('Git LFS could not materialize the selected dataset files.')


def _save_checkout_identity(checkout_path, identity):
    """Atomically add non-secret identity to a newly cloned checkout."""

    _validate_checkout_remote(checkout_path, identity, run=subprocess.run)
    config_path = find_local_config(checkout_path, required=True)
    configuration = Configuration.load(config_path)
    if configuration.checkout is not None and configuration.checkout.dataset_id != identity.dataset_id:
        raise GitError('The clone destination already contains another Niyān checkout identity.')
    configuration.set_checkout(identity)
    configuration.save(config_path)


def _validate_checkout_remote(cwd, identity, *, run):
    """Reject checkout metadata whose Git remote targets another repository."""

    try:
        result = run(['git', '-C', str(cwd), 'remote', 'get-url', identity.remote], check=False, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        raise GitError('Git could not validate the Niyān checkout remote.') from error
    if result.returncode != 0:
        raise GitError(f'The Niyān checkout remote {identity.remote!r} is unavailable.')
    remote_url = result.stdout.strip()
    parsed = urlparse(remote_url)
    expected_path = f'/git/{identity.dataset_id}.git'
    if _origin(remote_url) != _origin(identity.host) or parsed.path != expected_path or parsed.params or parsed.query or parsed.fragment:
        raise GitError('The Git remote does not match this checkout’s Niyān dataset identity.')


def credential_helper(*, host, username, token_id=None, storage=None, token_file=None, paths, stores, stdin=None, stdout=None):
    """Serve one host-restricted Git credential-helper request.

    Parameters
    ----------
    host : str
        Expected installation origin.
    username : str
        Informational Basic-authentication username.
    token_id : str, optional
        Stored access-token UUID.
    storage : str, optional
        Credential storage adapter name.
    token_file : pathlib.Path, optional
        Ephemeral mode-0600 environment-token file.
    paths : niyan.config.AppPaths
        CLI user paths.
    stores : niyan.credentials.CredentialStores
        Credential storage adapters.
    stdin : file-like object, optional
        Git credential request input.
    stdout : file-like object, optional
        Git credential response output.

    Returns
    -------
    int
        Process exit code.
    """

    request_stream = stdin or sys.stdin
    response_stream = stdout or sys.stdout
    request_fields = {}
    for line in request_stream:
        line = line.rstrip('\n')
        if not line:
            break
        key, separator, value = line.partition('=')
        if separator:
            request_fields[key] = value

    expected = urlparse(host)
    if request_fields.get('protocol') != expected.scheme or request_fields.get('host') != expected.netloc:
        return 1
    if token_file is not None:
        token = _read_ephemeral_token(Path(token_file))
    elif token_id and storage:
        token = stores.get(storage).get(host=host, token_id=token_id)
    else:
        raise CredentialError('Git credential helper configuration is incomplete.')
    print(f'username={username}', file=response_stream)
    print(f'password={token}', file=response_stream)
    print(file=response_stream)
    return 0


def _credential_helper_arguments(*, host, credential):
    """Build metadata-only helper arguments and optional secret handoff."""

    arguments = [sys.executable, '-m', 'niyan', '_credential-helper', '--host', host, '--username', credential.username]
    if credential.binding is not None:
        arguments.extend(['--token-id', credential.binding.token_id, '--storage', credential.binding.storage])
        return arguments, None

    temporary = tempfile.NamedTemporaryFile('w', encoding='utf-8', prefix='niyan-credential-', delete=False)
    try:
        temporary.write(credential.token)
        temporary.write('\n')
        temporary.close()
        token_path = Path(temporary.name)
        token_path.chmod(0o600)
    except Exception:
        temporary.close()
        Path(temporary.name).unlink(missing_ok=True)
        raise
    arguments.extend(['--token-file', str(token_path)])
    return arguments, token_path


def _read_ephemeral_token(path):
    """Read a private temporary token file created by the parent CLI."""

    try:
        if path.stat().st_mode & 0o077:
            raise CredentialError('Refusing a Git credential handoff file with broad permissions.')
        token = path.read_text().rstrip('\n')
    except OSError as error:
        raise CredentialError('The temporary Git credential is unavailable.') from error
    if not token:
        raise CredentialError('The temporary Git credential is empty.')
    return token


def _origin(url):
    """Return scheme and authority for Git URL validation."""

    parsed = urlparse(url)
    return parsed.scheme.lower(), parsed.netloc.lower()
