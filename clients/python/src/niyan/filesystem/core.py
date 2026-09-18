"""Read-only fsspec metadata access for Niyān datasets."""

import os
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from fsspec.spec import AbstractBufferedFile, AbstractFileSystem

from niyan.errors import ApiError, ConfigurationError, CredentialError
from niyan.filesystem.errors import NiyanAuthenticationError, NiyanCompatibilityError, NiyanNotFoundError, NiyanPermissionError, NiyanTransferError
from niyan.filesystem.path import canonical_name, options_from_url, parse_location, strip_protocol
from niyan.http import ApiClient, SameOriginRedirectHandler, normalize_host


REQUIRED_METADATA_FEATURES = frozenset({'dataset-path-resolution', 'exact-revision-resolution', 'repository-metadata'})
REQUIRED_READ_FEATURES = frozenset({'git-blob-reads', 'authorized-lfs-download-actions'})


class NiyanFileSystem(AbstractFileSystem):
    """Expose immutable Niyān dataset revisions through synchronous fsspec APIs."""

    protocol = 'niyan'
    root_marker = ''
    cachable = False

    def __init__(self, *, host: str | None = None, dataset: str | None = None, revision: str | None = None, token: str | None = None, timeout: float = 30, block_size: int = 5 * 1024 * 1024, environment: dict[str, str] | None = None, cwd: str | os.PathLike[str] | None = None, paths: Any = None, stores: Any = None, api_factory: Callable[..., ApiClient] = ApiClient, transfer_opener: Any = None, **storage_options: Any) -> None:
        """Initialize without reading configuration, credentials, Git, or the network.

        Parameters
        ----------
        host : str, optional
            Explicit Niyān installation origin.
        dataset : str, optional
            Dataset path used when method paths are repository-relative.
        revision : str, optional
            Branch, tag, or full commit selected by default.
        token : str, optional
            Explicit access token kept only in memory.
        timeout : float, optional
            REST request timeout in seconds.
        block_size : int, optional
            Maximum readahead block requested for ordinary streaming reads.
        environment : dict[str, str], optional
            Process-environment override used by tests and embedded callers.
        cwd : path-like, optional
            Working directory considered for checkout-local credential binding.
        paths : niyan.config.AppPaths, optional
            User path override used by tests.
        stores : niyan.credentials.CredentialStores, optional
            Credential-store override used by tests.
        api_factory : callable, optional
            REST client constructor.
        transfer_opener : urllib.request.OpenerDirector, optional
            HTTP transfer override used by tests.
        **storage_options
            Reserved fsspec construction options.
        """

        super().__init__(**storage_options)
        self.host = normalize_host(host) if host else None
        self.dataset = dataset.strip('/') if dataset else None
        self.revision = revision
        self._explicit_token = token
        self.timeout = timeout
        if block_size < 1:
            raise ValueError('Niyān filesystem block_size must be positive.')
        self.block_size = block_size
        self._environment = environment
        self._cwd = Path(cwd) if cwd is not None else None
        self._paths = paths
        self._stores = stores
        self._api_factory = api_factory
        self._transfer_opener = transfer_opener or build_opener(SameOriginRedirectHandler())
        self._capabilities_by_host: dict[str, dict[str, Any]] = {}

    @classmethod
    def _strip_protocol(cls, path: str | list[str]) -> str | list[str]:
        """Return fsspec paths without the protocol or installation authority."""

        if isinstance(path, list):
            return [strip_protocol(item) for item in path]
        return strip_protocol(path)

    @staticmethod
    def _get_kwargs_from_urls(path: str) -> dict[str, str]:
        """Extract host and revision storage options from a canonical URL."""

        return options_from_url(path)

    def ls(self, path: str, detail: bool = True, **kwargs: Any) -> list[dict[str, Any]] | list[str]:
        """List direct children while pinning the requested revision once."""

        context = self._resolve(path)
        items: list[dict[str, Any]] = []
        offset = 0
        while True:
            _, page = self._call(context.api.list_repository_tree, context.dataset_id, revision=context.resolved_commit, path=context.repository_path, limit=100, offset=offset)
            for entry in page['items']:
                repository_path = str(entry['path'])
                item_type = 'directory' if entry['object_type'] == 'tree' else 'file'
                items.append(
                    {
                        'name': canonical_name(host=context.host, dataset_path=context.dataset_path, repository_path=repository_path, resolved_commit=context.resolved_commit),
                        'type': item_type,
                        'size': 0 if item_type == 'directory' else int(entry['size']),
                        'dataset_id': context.dataset_id,
                        'dataset_path': context.dataset_path,
                        'repository_path': repository_path,
                        'resolved_commit': context.resolved_commit,
                        'object_id': entry['object_id'],
                        'is_lfs': bool(entry['is_lfs']),
                        'lfs_object_id': entry['lfs_object_id'],
                    }
                )
            next_offset = page.get('next_offset')
            if next_offset is None:
                break
            offset = int(next_offset)
        return items if detail else [item['name'] for item in items]

    def info(self, path: str, **kwargs: Any) -> dict[str, Any]:
        """Return directory or logical file metadata at one exact commit."""

        context = self._resolve(path)
        if not context.repository_path:
            return self._directory_info(context)
        try:
            _, blob = self._call(context.api.get_repository_blob, context.dataset_id, revision=context.resolved_commit, path=context.repository_path)
        except NiyanNotFoundError as error:
            if error.resource_kind != 'path':
                raise
            self._call(context.api.list_repository_tree, context.dataset_id, revision=context.resolved_commit, path=context.repository_path, limit=1, offset=0)
            return self._directory_info(context)
        return {
            'name': canonical_name(host=context.host, dataset_path=context.dataset_path, repository_path=context.repository_path, resolved_commit=context.resolved_commit),
            'type': 'file',
            'size': int(blob['lfs_size'] if blob['is_lfs'] else blob['size']),
            'dataset_id': context.dataset_id,
            'dataset_path': context.dataset_path,
            'repository_path': context.repository_path,
            'resolved_commit': context.resolved_commit,
            'object_id': blob['object_id'],
            'is_lfs': bool(blob['is_lfs']),
            'lfs_object_id': blob['lfs_object_id'],
        }

    def exists(self, path: str, **kwargs: Any) -> bool:
        """Return false only for a missing resource and preserve access failures."""

        try:
            self.info(path, **kwargs)
            return True
        except NiyanNotFoundError:
            return False

    def isfile(self, path: str) -> bool:
        """Return whether a visible path is a file without hiding access errors."""

        try:
            return self.info(path)['type'] == 'file'
        except NiyanNotFoundError:
            return False

    def isdir(self, path: str) -> bool:
        """Return whether a visible path is a directory without hiding access errors."""

        try:
            return self.info(path)['type'] == 'directory'
        except NiyanNotFoundError:
            return False

    def _open(self, path: str, mode: str = 'rb', block_size: int | None = None, cache_options: dict[str, Any] | None = None, **kwargs: Any) -> '_NiyanBufferedFile':
        """Open one immutable file for bounded synchronous reads and seeks."""

        if mode != 'rb':
            raise PermissionError('The Niyān filesystem is read-only; open files with mode="rb".')
        context = self._resolve(path)
        if not context.repository_path:
            raise IsADirectoryError(path)
        self._require_read_capabilities(host=context.host, api=context.api)
        action = self._authorize_context(context)
        selected_block_size = self.block_size if block_size in (None, 'default') else int(block_size)
        return _NiyanBufferedFile(self, context=context, action=action, block_size=selected_block_size, cache_options=cache_options)

    def cat_file(self, path: str, start: int | None = None, end: int | None = None, **kwargs: Any) -> bytes:
        """Read a requested byte interval without materializing unrelated content."""

        context = self._resolve(path)
        if not context.repository_path:
            raise IsADirectoryError(path)
        self._require_read_capabilities(host=context.host, api=context.api)
        action = self._authorize_context(context)
        size = int(action['size'])
        normalized_start = 0 if start is None else int(start)
        normalized_end = size if end is None else int(end)
        if normalized_start < 0:
            normalized_start = max(0, size + normalized_start)
        if normalized_end < 0:
            normalized_end = max(0, size + normalized_end)
        normalized_start = min(size, normalized_start)
        normalized_end = min(size, normalized_end)
        if normalized_end <= normalized_start:
            return b''
        content, _ = self._read_action_range(context=context, action=action, start=normalized_start, end=normalized_end)
        return content

    def _resolve(self, path: str) -> '_ResolvedPath':
        """Resolve host, credential, dataset boundary, and exact commit lazily."""

        location = parse_location(path, configured_host=self.host, configured_dataset=self.dataset, configured_revision=self.revision)
        host = location.host or self._resolve_configured_host()
        token = self._select_token(host=host, locator_path=location.locator_path)
        api = self._api_factory(host, token=token, timeout=self.timeout)
        self._require_metadata_capabilities(host=host, api=api)
        _, dataset = self._call(api.resolve_dataset, location.locator_path)
        _, resolution = self._call(api.resolve_revision, str(dataset['id']), revision=location.revision)
        return _ResolvedPath(
            api=api,
            token=token,
            host=host,
            dataset_id=str(dataset['id']),
            dataset_path=str(dataset['path']),
            repository_path=str(dataset.get('repository_path') or ''),
            resolved_commit=str(resolution['resolved_commit']),
        )

    def _authorize_context(self, context: '_ResolvedPath') -> dict[str, Any]:
        """Obtain and validate one short-lived action for an exact path."""

        _, action = self._call(context.api.authorize_repository_download, context.dataset_id, revision=context.resolved_commit, path=context.repository_path)
        if action.get('resolved_commit') != context.resolved_commit or action.get('path') != context.repository_path or action.get('storage') not in ('git', 'lfs') or action.get('method') != 'GET':
            raise NiyanTransferError('Niyān returned an inconsistent file transfer action.')
        if not isinstance(action.get('size'), int) or action['size'] < 0 or not isinstance(action.get('object_id'), str):
            raise NiyanTransferError('Niyān returned incomplete file transfer metadata.')
        if action['storage'] == 'lfs' and not isinstance(action.get('lfs_object_id'), str):
            raise NiyanTransferError('Niyān returned incomplete Git LFS transfer metadata.')
        parsed_url = urlsplit(str(action.get('url') or ''))
        if parsed_url.scheme not in ('http', 'https') or not parsed_url.netloc or parsed_url.username or parsed_url.password:
            raise NiyanTransferError('Niyān returned an invalid file transfer action.')
        return action

    def _read_action_range(self, *, context: '_ResolvedPath', action: dict[str, Any], start: int, end: int, allow_refresh: bool = True) -> tuple[bytes, dict[str, Any]]:
        """Read one exact range and safely refresh an expired LFS action once."""

        size = int(action['size'])
        if not 0 <= start <= end <= size:
            raise ValueError('The requested byte range is outside the file.')
        if start == end:
            return b'', action
        if not action.get('range_supported') and (start != 0 or end != size):
            raise NiyanCompatibilityError('This file transfer does not support byte ranges.')
        headers = {str(name): str(value) for name, value in dict(action.get('headers') or {}).items()}
        headers['Range'] = f'bytes={start}-{end - 1}'
        if action['storage'] == 'git':
            if _origin(str(action['url'])) != _origin(context.host):
                raise NiyanTransferError('Niyān returned a Git blob action outside the selected installation.')
            headers['Authorization'] = f'Bearer {context.token}'
        request = Request(str(action['url']), headers=headers, method='GET')
        try:
            with self._transfer_opener.open(request, timeout=self.timeout) as response:
                expected_length = end - start
                content = response.read(expected_length + 1)
                status = getattr(response, 'status', None) or response.getcode()
                if len(content) != expected_length:
                    raise NiyanTransferError('The file transfer returned an unexpected number of bytes.')
                if status == 206:
                    expected_content_range = f'bytes {start}-{end - 1}/{size}'
                    if response.headers.get('Content-Range') != expected_content_range:
                        raise NiyanTransferError('The file transfer returned an inconsistent byte range.')
                elif status != 200 or start != 0 or end != size:
                    raise NiyanTransferError('The file transfer did not honor the requested byte range.')
                return content, action
        except HTTPError as error:
            if action['storage'] == 'lfs' and allow_refresh and error.code in (401, 403):
                refreshed = self._authorize_context(context)
                _require_same_object(action, refreshed)
                return self._read_action_range(context=context, action=refreshed, start=start, end=end, allow_refresh=False)
            if action['storage'] == 'git' and error.code == 401:
                raise NiyanAuthenticationError('The selected Niyān credential is missing, invalid, expired, or revoked.') from None
            if action['storage'] == 'git' and error.code == 403:
                raise NiyanPermissionError('The selected Niyān credential does not permit this operation.') from None
            raise NiyanTransferError('The authorized file transfer failed.') from None
        except URLError as error:
            raise NiyanTransferError('The authorized file transfer could not be completed.') from error

    def _resolve_configured_host(self) -> str:
        """Discover a configured host only when an operation actually needs it."""

        from niyan.auth import resolve_host
        from niyan.config import AppPaths

        paths = self._paths or AppPaths.from_environment(self._environment)
        try:
            return resolve_host(None, paths=paths, cwd=self._cwd, environment=self._environment)
        except ConfigurationError as error:
            raise NiyanAuthenticationError(str(error)) from error

    def _select_token(self, *, host: str, locator_path: str) -> str:
        """Select explicit, environment, checkout-local, or user credentials lazily."""

        if self._explicit_token is not None:
            return self._explicit_token
        from niyan.auth import select_credential
        from niyan.config import AppPaths
        from niyan.credentials import CredentialStores

        paths = self._paths or AppPaths.from_environment(self._environment)
        stores = self._stores or CredentialStores(paths=paths)
        try:
            return select_credential(host=host, dataset_path=locator_path, paths=paths, stores=stores, cwd=self._cwd, environment=self._environment).token
        except (ConfigurationError, CredentialError) as error:
            raise NiyanAuthenticationError(str(error)) from error

    def _require_metadata_capabilities(self, *, host: str, api: ApiClient) -> None:
        """Reject incompatible servers before requesting repository metadata."""

        capabilities = self._capabilities_by_host.get(host)
        if capabilities is None:
            _, capabilities = self._call(api.capabilities)
            self._capabilities_by_host[host] = capabilities
        filesystem = capabilities.get('filesystem') if isinstance(capabilities, dict) else None
        features = set(filesystem.get('features', [])) if isinstance(filesystem, dict) else set()
        api_versions = capabilities.get('api_versions', []) if isinstance(capabilities, dict) else []
        if 'v1' not in api_versions or not isinstance(filesystem, dict) or filesystem.get('protocol_version') != 1 or not REQUIRED_METADATA_FEATURES.issubset(features):
            raise NiyanCompatibilityError('The Niyān installation does not support filesystem metadata protocol version 1.')

    def _require_read_capabilities(self, *, host: str, api: ApiClient) -> None:
        """Require the content-read features used by binary file handles."""

        self._require_metadata_capabilities(host=host, api=api)
        filesystem = self._capabilities_by_host[host]['filesystem']
        if not REQUIRED_READ_FEATURES.issubset(set(filesystem.get('features', []))):
            raise NiyanCompatibilityError('The Niyān installation does not support filesystem content reads.')

    def _call(self, operation: Callable[..., tuple[int, dict[str, Any]]], *args: Any, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        """Map sanitized REST failures into the public filesystem error hierarchy."""

        try:
            return operation(*args, **kwargs)
        except ApiError as error:
            if error.status == 401:
                raise NiyanAuthenticationError('The selected Niyān credential is missing, invalid, expired, or revoked.') from error
            if error.status == 403:
                raise NiyanPermissionError('The selected Niyān credential does not permit this operation.') from error
            if error.status == 404:
                resource_kind = error.code.removesuffix('_not_found') if error.code and error.code.endswith('_not_found') else None
                raise NiyanNotFoundError(str(error), resource_kind=resource_kind) from error
            raise NiyanTransferError(str(error)) from error

    @staticmethod
    def _directory_info(context: '_ResolvedPath') -> dict[str, Any]:
        """Build standard fsspec directory metadata from a resolved path."""

        return {
            'name': canonical_name(host=context.host, dataset_path=context.dataset_path, repository_path=context.repository_path, resolved_commit=context.resolved_commit),
            'type': 'directory',
            'size': 0,
            'dataset_id': context.dataset_id,
            'dataset_path': context.dataset_path,
            'repository_path': context.repository_path,
            'resolved_commit': context.resolved_commit,
        }


class _ResolvedPath:
    """Carry exact operation context without exposing credentials."""

    def __init__(self, *, api: ApiClient, token: str, host: str, dataset_id: str, dataset_path: str, repository_path: str, resolved_commit: str) -> None:
        self.api = api
        self.token = token
        self.host = host
        self.dataset_id = dataset_id
        self.dataset_path = dataset_path
        self.repository_path = repository_path
        self.resolved_commit = resolved_commit


class _NiyanBufferedFile(AbstractBufferedFile):
    """Bind an fsspec file handle to one exact commit and transfer action."""

    def __init__(self, fs: NiyanFileSystem, *, context: _ResolvedPath, action: dict[str, Any], block_size: int, cache_options: dict[str, Any] | None) -> None:
        self.context = context
        self.action = action
        path = canonical_name(host=context.host, dataset_path=context.dataset_path, repository_path=context.repository_path, resolved_commit=context.resolved_commit)
        super().__init__(fs, path, mode='rb', block_size=block_size, cache_type='readahead', cache_options=cache_options, size=int(action['size']))

    def _fetch_range(self, start: int, end: int) -> bytes:
        """Fetch one bounded block and retain a safely refreshed action."""

        if start >= self.size:
            return b''
        content, self.action = self.fs._read_action_range(context=self.context, action=self.action, start=start, end=min(end, self.size))
        return content


def _origin(url: str) -> tuple[str, str]:
    """Return a normalized HTTP origin for credential-forwarding checks."""

    parsed = urlsplit(url)
    return parsed.scheme.lower(), parsed.netloc.lower()


def _require_same_object(previous: dict[str, Any], refreshed: dict[str, Any]) -> None:
    """Reject a refreshed action that no longer identifies the same bytes."""

    identity_fields = ('resolved_commit', 'path', 'size', 'object_id', 'lfs_object_id', 'storage')
    if any(previous.get(field) != refreshed.get(field) for field in identity_fields):
        raise NiyanTransferError('Niyān refreshed the transfer action with inconsistent object metadata.')
