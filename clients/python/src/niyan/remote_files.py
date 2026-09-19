import os
import sys
from pathlib import Path, PurePosixPath

from niyan.config import normalize_dataset_path
from niyan.errors import NiyanCliError
from niyan.filesystem import NiyanFileSystem, NiyanFileSystemError


class RemoteFileError(NiyanCliError):
    """Report a sanitized checkout-free repository operation failure."""


def show_remote_tree(*, host, dataset_path, repository_path='', revision=None, paths, stores, cwd=None, stdout=None, filesystem_factory=NiyanFileSystem):
    """List one remote directory through the pinned filesystem client."""

    output = stdout or sys.stdout
    filesystem = _filesystem(host=host, dataset_path=dataset_path, revision=revision, paths=paths, stores=stores, cwd=cwd, factory=filesystem_factory)
    try:
        items = filesystem.ls(_normalize_repository_path(repository_path), detail=True)
    except (NiyanFileSystemError, OSError, ValueError) as error:
        raise RemoteFileError(str(error)) from error
    print('TYPE\tSIZE\tPATH', file=output)
    for item in items:
        item_type = 'tree' if item['type'] == 'directory' else ('lfs' if item.get('is_lfs') else 'blob')
        size = '-' if item['type'] == 'directory' else str(item['size'])
        print(f"{item_type}\t{size}\t{item['repository_path']}", file=output)
    return items


def stream_remote_file(*, host, dataset_path, repository_path, revision=None, paths, stores, cwd=None, stdout=None, filesystem_factory=NiyanFileSystem):
    """Stream exactly one remote file to a binary output without diagnostics."""

    output = stdout or sys.stdout.buffer
    filesystem = _filesystem(host=host, dataset_path=dataset_path, revision=revision, paths=paths, stores=stores, cwd=cwd, factory=filesystem_factory)
    try:
        with filesystem.open(_normalize_repository_path(repository_path), 'rb') as source:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
    except (NiyanFileSystemError, OSError, ValueError) as error:
        raise RemoteFileError(str(error)) from error


def download_remote_paths(*, host, dataset_path, repository_paths, revision=None, output_directory=None, paths, stores, cwd=None, filesystem_factory=NiyanFileSystem):
    """Download selected repository paths through atomic filesystem transfers."""

    normalized_dataset = normalize_dataset_path(dataset_path)
    working_directory = Path(cwd or Path.cwd())
    initial = _filesystem(host=host, dataset_path=normalized_dataset, revision=revision, paths=paths, stores=stores, cwd=working_directory, factory=filesystem_factory)
    try:
        root = initial.info('')
        resolved_commit = str(root['resolved_commit'])
        filesystem = _filesystem(host=host, dataset_path=normalized_dataset, revision=resolved_commit, paths=paths, stores=stores, cwd=working_directory, factory=filesystem_factory)
        destination_root = Path(output_directory) if output_directory is not None else Path(normalized_dataset.rsplit('/', 1)[-1])
        if not destination_root.is_absolute():
            destination_root = working_directory / destination_root
        selected_paths = [_normalize_repository_path(path) for path in repository_paths] if repository_paths else ['']
        for repository_path in selected_paths:
            info = filesystem.info(repository_path)
            destination = destination_root if not repository_path else destination_root.joinpath(*PurePosixPath(repository_path).parts)
            if info['type'] == 'directory':
                filesystem.get(repository_path, destination, recursive=True)
            else:
                filesystem.get_file(repository_path, destination)
    except (NiyanFileSystemError, OSError, ValueError) as error:
        raise RemoteFileError(str(error)) from error
    return {'dataset_path': normalized_dataset, 'resolved_commit': resolved_commit, 'output_directory': os.fspath(destination_root), 'paths': selected_paths}


def _filesystem(*, host, dataset_path, revision, paths, stores, cwd, factory):
    """Construct the shared filesystem client with CLI-selected credentials."""

    return factory(host=host, dataset=normalize_dataset_path(dataset_path), revision=revision, paths=paths, stores=stores, cwd=Path(cwd or Path.cwd()))


def _normalize_repository_path(path):
    """Normalize a safe repository-relative path for remote operations."""

    if not isinstance(path, str):
        raise ValueError('Repository paths must be strings.')
    normalized = path.strip('/')
    if normalized and any(component in ('', '.', '..') for component in normalized.split('/')):
        raise ValueError('Repository paths cannot contain empty, current-directory, or parent-directory components.')
    return normalized
