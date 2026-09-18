from datetime import datetime
from typing import Literal
from urllib.parse import urlencode
from uuid import UUID

from django.http import StreamingHttpResponse
from django.utils.http import content_disposition_header
from ninja import Query, Router, Schema, Status

from accounts.authentication import require_access, session_or_access_token
from datasets.lfs_transfers import LfsTransferUnavailable, issue_download_action
from datasets.models import LfsObject
from datasets.object_storage import ObjectStoreError
from datasets.repository_browser import InvalidRepositoryInput, LfsContentUnavailable, RepositoryBrowseError, RepositoryBrowser, RepositoryPathNotFound, RevisionNotFound
from datasets.selectors import get_visible_dataset


class ErrorResponse(Schema):
    """Provide a stable repository browsing error."""

    code: str
    detail: str


class RefResponse(Schema):
    """Describe one branch or tag ref."""

    name: str
    full_name: str
    object_id: str
    object_type: str


class RefListResponse(Schema):
    """Return one bounded page of ordered refs."""

    kind: str
    limit: int
    offset: int
    next_offset: int | None
    items: list[RefResponse]


class CommitResponse(Schema):
    """Describe one immutable Git commit."""

    object_id: str
    parent_ids: list[str]
    author_name: str
    author_email: str
    authored_at: datetime
    subject: str


class CommitListResponse(Schema):
    """Return commits reachable from one resolved revision."""

    resolved_commit: str
    limit: int
    offset: int
    next_offset: int | None
    items: list[CommitResponse]


class TreeEntryResponse(Schema):
    """Describe one direct child in a Git tree."""

    name: str
    path: str
    mode: str
    object_type: str
    object_id: str
    size: int | None


class TreeListResponse(Schema):
    """Return one directory listing at an exact commit."""

    resolved_commit: str
    path: str
    limit: int
    offset: int
    next_offset: int | None
    items: list[TreeEntryResponse]


class BlobMetadataResponse(Schema):
    """Describe one Git-resident file or LFS pointer."""

    resolved_commit: str
    path: str
    object_id: str
    size: int
    is_lfs: bool
    lfs_object_id: str | None
    lfs_size: int | None


class ReadmeResponse(BlobMetadataResponse):
    """Return root README text together with exact blob metadata."""

    content: str


class DownloadActionResponse(Schema):
    """Authorize a browser download through Git or direct object storage."""

    resolved_commit: str
    path: str
    size: int
    storage: Literal['git', 'lfs']
    method: str
    url: str
    headers: dict[str, str]
    expires_in: int | None


router = Router(tags=['repository'], auth=session_or_access_token)
MAX_LIST_OFFSET = 10_000


def get_browser(request, dataset_id):
    """Authorize and open a dataset repository for browsing."""

    require_access(request=request, scope='read_repository', dataset_id=dataset_id)
    dataset = get_visible_dataset(dataset_id=dataset_id, user=request.auth)
    if dataset is None:
        return None
    return RepositoryBrowser(dataset)


def serialize_blob(resolved_commit, metadata):
    """Convert internal blob metadata into the public response shape."""

    return {
        'resolved_commit': resolved_commit,
        'path': metadata.path,
        'object_id': metadata.object_id,
        'size': metadata.size,
        'is_lfs': metadata.lfs_object_id is not None,
        'lfs_object_id': metadata.lfs_object_id,
        'lfs_size': metadata.lfs_size,
    }


@router.get('/{dataset_id}/repository/refs', response={200: RefListResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 422: ErrorResponse, 503: ErrorResponse})
def list_repository_refs_endpoint(request, dataset_id: UUID, kind: Literal['branches', 'tags'] = 'branches', limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0, le=MAX_LIST_OFFSET)):
    """List branches or tags directly from the bare repository."""

    try:
        browser = get_browser(request, dataset_id)
        if browser is None:
            return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
        items, next_offset = browser.list_refs(kind=kind, limit=limit, offset=offset)
        return {'kind': kind, 'limit': limit, 'offset': offset, 'next_offset': next_offset, 'items': items}
    except InvalidRepositoryInput:
        return Status(422, {'code': 'validation_error', 'detail': 'The repository request is invalid.'})
    except RepositoryBrowseError:
        return Status(503, {'code': 'repository_unavailable', 'detail': 'The dataset repository could not be read.'})


@router.get('/{dataset_id}/repository/commits', response={200: CommitListResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 422: ErrorResponse, 503: ErrorResponse})
def list_repository_commits_endpoint(request, dataset_id: UUID, revision: str = 'main', limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=MAX_LIST_OFFSET)):
    """List commits reachable from a branch, tag, or commit."""

    try:
        browser = get_browser(request, dataset_id)
        if browser is None:
            return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
        resolved_commit, items, next_offset = browser.list_commits(revision=revision, limit=limit, offset=offset)
        return {'resolved_commit': resolved_commit, 'limit': limit, 'offset': offset, 'next_offset': next_offset, 'items': items}
    except RevisionNotFound:
        return Status(404, {'code': 'revision_not_found', 'detail': 'The requested revision does not exist.'})
    except InvalidRepositoryInput:
        return Status(422, {'code': 'validation_error', 'detail': 'The repository request is invalid.'})
    except RepositoryBrowseError:
        return Status(503, {'code': 'repository_unavailable', 'detail': 'The dataset repository could not be read.'})


@router.get('/{dataset_id}/repository/tree', response={200: TreeListResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 422: ErrorResponse, 503: ErrorResponse})
def list_repository_tree_endpoint(request, dataset_id: UUID, revision: str = 'main', path: str = '', limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0, le=MAX_LIST_OFFSET)):
    """List direct children of a repository directory."""

    try:
        browser = get_browser(request, dataset_id)
        if browser is None:
            return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
        resolved_commit, items, next_offset = browser.list_tree(revision=revision, path=path, limit=limit, offset=offset)
        return {'resolved_commit': resolved_commit, 'path': path.strip('/'), 'limit': limit, 'offset': offset, 'next_offset': next_offset, 'items': items}
    except RevisionNotFound:
        return Status(404, {'code': 'revision_not_found', 'detail': 'The requested revision does not exist.'})
    except RepositoryPathNotFound:
        return Status(404, {'code': 'path_not_found', 'detail': 'The requested repository path does not exist.'})
    except InvalidRepositoryInput:
        return Status(422, {'code': 'validation_error', 'detail': 'The repository request is invalid.'})
    except RepositoryBrowseError:
        return Status(503, {'code': 'repository_unavailable', 'detail': 'The dataset repository could not be read.'})


@router.get('/{dataset_id}/repository/blob', response={200: BlobMetadataResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 422: ErrorResponse, 503: ErrorResponse})
def get_repository_blob_endpoint(request, dataset_id: UUID, path: str, revision: str = 'main'):
    """Return Git and optional LFS metadata for one file."""

    try:
        browser = get_browser(request, dataset_id)
        if browser is None:
            return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
        resolved_commit, metadata = browser.get_blob_metadata(revision=revision, path=path)
        return serialize_blob(resolved_commit, metadata)
    except RevisionNotFound:
        return Status(404, {'code': 'revision_not_found', 'detail': 'The requested revision does not exist.'})
    except RepositoryPathNotFound:
        return Status(404, {'code': 'path_not_found', 'detail': 'The requested repository path does not exist.'})
    except InvalidRepositoryInput:
        return Status(422, {'code': 'validation_error', 'detail': 'The repository request is invalid.'})
    except RepositoryBrowseError:
        return Status(503, {'code': 'repository_unavailable', 'detail': 'The dataset repository could not be read.'})


@router.get('/{dataset_id}/repository/blob/raw', response={200: None, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse, 422: ErrorResponse, 503: ErrorResponse})
def download_repository_blob_endpoint(request, dataset_id: UUID, path: str, revision: str = 'main'):
    """Stream one ordinary Git-resident blob without buffering it."""

    try:
        browser = get_browser(request, dataset_id)
        if browser is None:
            return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
        resolved_commit, metadata, content = browser.open_blob(revision=revision, path=path)
        response = StreamingHttpResponse(content, content_type='application/octet-stream')
        response['Content-Length'] = str(metadata.size)
        response['Content-Disposition'] = content_disposition_header(True, metadata.path.rsplit('/', 1)[-1])
        response['ETag'] = f'"{metadata.object_id}"'
        response['X-Niyan-Resolved-Commit'] = resolved_commit
        return response
    except LfsContentUnavailable:
        return Status(409, {'code': 'lfs_content_unavailable', 'detail': 'Git LFS content is not available yet.'})
    except RevisionNotFound:
        return Status(404, {'code': 'revision_not_found', 'detail': 'The requested revision does not exist.'})
    except RepositoryPathNotFound:
        return Status(404, {'code': 'path_not_found', 'detail': 'The requested repository path does not exist.'})
    except InvalidRepositoryInput:
        return Status(422, {'code': 'validation_error', 'detail': 'The repository request is invalid.'})
    except RepositoryBrowseError:
        return Status(503, {'code': 'repository_unavailable', 'detail': 'The dataset repository could not be read.'})


@router.get('/{dataset_id}/repository/download', response={200: DownloadActionResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse, 422: ErrorResponse, 503: ErrorResponse})
def authorize_repository_download_endpoint(request, dataset_id: UUID, path: str, revision: str = 'main'):
    """Authorize a browser download without proxying Git LFS object bytes."""

    try:
        browser = get_browser(request, dataset_id)
        if browser is None:
            return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
        resolved_commit, metadata = browser.get_blob_metadata(revision=revision, path=path)
        if metadata.lfs_object_id is None:
            query = urlencode({'path': metadata.path, 'revision': resolved_commit})
            return {
                'resolved_commit': resolved_commit,
                'path': metadata.path,
                'size': metadata.size,
                'storage': 'git',
                'method': 'GET',
                'url': request.build_absolute_uri(f'/api/v1/datasets/{dataset_id}/repository/blob/raw?{query}'),
                'headers': {},
                'expires_in': None,
            }

        lfs_object = LfsObject.objects.select_related('dataset').filter(dataset_id=dataset_id, oid=metadata.lfs_object_id, size=metadata.lfs_size, state__in=[LfsObject.State.AVAILABLE, LfsObject.State.REFERENCED]).first()
        if lfs_object is None:
            return Status(409, {'code': 'lfs_content_unavailable', 'detail': 'Git LFS content is not available yet.'})
        action = issue_download_action(lfs_object=lfs_object)
        return {
            'resolved_commit': resolved_commit,
            'path': metadata.path,
            'size': metadata.lfs_size,
            'storage': 'lfs',
            'method': action.method,
            'url': action.url,
            'headers': action.headers,
            'expires_in': action.expires_in,
        }
    except RevisionNotFound:
        return Status(404, {'code': 'revision_not_found', 'detail': 'The requested revision does not exist.'})
    except RepositoryPathNotFound:
        return Status(404, {'code': 'path_not_found', 'detail': 'The requested repository path does not exist.'})
    except InvalidRepositoryInput:
        return Status(422, {'code': 'validation_error', 'detail': 'The repository request is invalid.'})
    except (LfsTransferUnavailable, ObjectStoreError):
        return Status(503, {'code': 'download_unavailable', 'detail': 'The download is temporarily unavailable.'})
    except RepositoryBrowseError:
        return Status(503, {'code': 'repository_unavailable', 'detail': 'The dataset repository could not be read.'})


@router.get('/{dataset_id}/repository/readme', response={200: ReadmeResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse, 422: ErrorResponse, 503: ErrorResponse})
def get_repository_readme_endpoint(request, dataset_id: UUID, revision: str = 'main'):
    """Return the conventional root README at one exact commit."""

    try:
        browser = get_browser(request, dataset_id)
        if browser is None:
            return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
        resolved_commit, metadata, content = browser.read_readme(revision=revision)
        response = serialize_blob(resolved_commit, metadata)
        response['content'] = content
        return response
    except LfsContentUnavailable:
        return Status(409, {'code': 'readme_unavailable', 'detail': 'The repository README cannot be displayed inline.'})
    except RevisionNotFound:
        return Status(404, {'code': 'revision_not_found', 'detail': 'The requested revision does not exist.'})
    except RepositoryPathNotFound:
        return Status(404, {'code': 'readme_not_found', 'detail': 'The repository has no root README.'})
    except InvalidRepositoryInput:
        return Status(422, {'code': 'validation_error', 'detail': 'The repository request is invalid.'})
    except RepositoryBrowseError:
        return Status(503, {'code': 'repository_unavailable', 'detail': 'The dataset repository could not be read.'})
