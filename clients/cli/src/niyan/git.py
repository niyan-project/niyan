import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from niyan.auth import select_credential
from niyan.errors import CredentialError, GitError
from niyan.http import ApiClient


def clone_dataset(*, host, dataset_path, destination, paths, stores, cwd=None, environment=None, api_factory=ApiClient, stderr=None):
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
    if shutil.which('git') is None:
        raise GitError('Git is required to clone Niyān datasets.')

    helper_arguments, temporary_secret = _credential_helper_arguments(host=host, credential=credential)
    helper_config = f'!{shlex.join(helper_arguments)}'
    command = [
        'git',
        '-c',
        'credential.helper=',
        '-c',
        f'credential.helper={helper_config}',
        '-c',
        'credential.useHttpPath=true',
        'clone',
        '--',
        git_url,
        str(destination_path),
    ]
    child_environment = dict(environment if environment is not None else os.environ)
    child_environment.pop('NIYAN_TOKEN', None)
    # LFS transfer is a later protocol slice. Prevent an installed Git LFS filter from making unauthenticated transfer attempts during this metadata-only clone.
    child_environment['GIT_LFS_SKIP_SMUDGE'] = '1'
    try:
        result = subprocess.run(command, cwd=working_directory, env=child_environment, check=False)
    except OSError as error:
        raise GitError('Git could not be started.') from error
    finally:
        if temporary_secret is not None:
            temporary_secret.unlink(missing_ok=True)
    if result.returncode != 0:
        raise GitError('Git could not clone the requested dataset.')
    print('Clone complete. Git LFS content is not downloaded in this version; LFS-tracked paths remain pointer files.', file=output)
    return destination_path


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
