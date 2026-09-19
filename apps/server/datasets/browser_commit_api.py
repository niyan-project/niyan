from datetime import datetime
from typing import Literal
from uuid import UUID

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from ninja import Field, Query, Router, Schema, Status

from accounts.authentication import get_access_token, require_access, session_or_access_token
from datasets.audit import record_audit_event
from datasets.browser_commits import BrowserCommitUnavailable, BrowserDraftConflict, BrowserDraftForbidden, BrowserDraftInvalid, create_browser_draft, discard_browser_draft, publish_browser_draft, stage_delete, stage_git_blob, stage_lfs_object
from datasets.lfs_transfers import LfsIntegrityError, LfsObjectMissing, LfsTransferUnavailable, finalize_lfs_upload, initiate_multipart_upload, issue_upload_action
from datasets.models import AuditEvent, BrowserCommitDraft, LfsObject
from datasets.object_storage import ObjectStoreError
from datasets.policies import can_write_repository
from datasets.selectors import get_visible_dataset


class ErrorResponse(Schema):
    """Provide a stable browser-commit error shape."""

    code: str
    detail: str


class DraftCreateInput(Schema):
    """Select the one branch a new browser draft targets."""

    target_branch: str = Field(min_length=1, max_length=255)


class DraftCommitInput(Schema):
    """Provide the explicit ordinary Git commit message."""

    message: str = Field(min_length=1, max_length=100_000)


class LfsStageInput(Schema):
    """Stage one client-hashed LFS file before direct transfer."""

    path: str = Field(min_length=1, max_length=4096)
    oid: str = Field(min_length=64, max_length=64)
    size: int = Field(ge=0, lt=2**63)


class DraftChangeResponse(Schema):
    """Expose one final staged path operation without storage credentials."""

    path: str
    operation: str
    storage: str
    size: int | None
    oid: str | None
    ready: bool


class DraftResponse(Schema):
    """Expose creator-visible draft identity and staged review state."""

    id: UUID
    dataset_id: UUID
    target_branch: str
    base_commit: str | None
    state: str
    committed_oid: str | None
    expires_at: datetime
    changes: list[DraftChangeResponse]


class DraftListResponse(Schema):
    """Return current creator-owned open drafts for resumption."""

    count: int
    items: list[DraftResponse]


class DirectUploadActionResponse(Schema):
    """Describe one short-lived direct S3 upload action."""

    method: Literal['PUT']
    href: str
    header: dict[str, str]
    expires_in: int


class MultipartLayoutResponse(Schema):
    """Describe one server-selected multipart upload layout."""

    session_id: UUID
    part_size: int
    part_count: int


class LfsStageResponse(Schema):
    """Tell the browser whether content is reusable, direct, or multipart."""

    change: DraftChangeResponse
    transfer: Literal['existing', 'basic', 'multipart']
    upload: DirectUploadActionResponse | None
    multipart: MultipartLayoutResponse | None


router = Router(tags=['browser-commits'], auth=session_or_access_token)


def _writable_dataset(request, dataset_id):
    """Resolve one visible dataset after credential and current-role checks."""

    require_access(request=request, scope='write_repository', dataset_id=dataset_id)
    dataset = get_visible_dataset(dataset_id=dataset_id, user=request.auth)
    if dataset is None:
        return None
    if not can_write_repository(user=request.auth, dataset=dataset):
        raise PermissionDenied
    return dataset


def _creator_draft(request, dataset, draft_id):
    """Return only the requesting creator's draft within the selected dataset."""

    return BrowserCommitDraft.objects.select_related('dataset', 'created_by').filter(pk=draft_id, dataset=dataset, created_by=request.auth).first()


def _serialize_change(change):
    """Serialize one staged operation and current LFS readiness."""

    oid = change.git_blob_oid or (change.lfs_object.oid if change.lfs_object_id else None)
    ready = change.operation == 'delete' or change.storage == 'git' or (change.lfs_object is not None and change.lfs_object.state in {LfsObject.State.AVAILABLE, LfsObject.State.REFERENCED})
    return {'path': change.path, 'operation': change.operation, 'storage': change.storage, 'size': change.size, 'oid': oid, 'ready': ready}


def _serialize_draft(draft):
    """Serialize one draft with an ordered final operation list."""

    changes = list(draft.changes.select_related('lfs_object').order_by('path'))
    return {
        'id': draft.id,
        'dataset_id': draft.dataset_id,
        'target_branch': draft.target_branch,
        'base_commit': draft.base_commit or None,
        'state': draft.state,
        'committed_oid': draft.committed_oid or None,
        'expires_at': draft.expires_at,
        'changes': [_serialize_change(change) for change in changes],
    }


def _draft_error(error):
    """Map sanitized domain failures to stable API responses."""

    if isinstance(error, BrowserDraftConflict):
        return Status(409, {'code': 'draft_conflict', 'detail': str(error)})
    if isinstance(error, BrowserDraftForbidden):
        return Status(403, {'code': 'permission_denied', 'detail': str(error)})
    if isinstance(error, BrowserDraftInvalid):
        return Status(422, {'code': 'draft_invalid', 'detail': str(error)})
    return Status(503, {'code': 'repository_unavailable', 'detail': 'The dataset repository operation is temporarily unavailable.'})


def _draft_rejection_reason(error):
    """Map one sanitized domain error to a stable audit reason code."""

    if isinstance(error, BrowserDraftConflict):
        return 'branch_moved'
    if isinstance(error, BrowserDraftForbidden):
        return 'permission_denied'
    if isinstance(error, BrowserDraftInvalid):
        return 'validation_failed'
    return 'repository_unavailable'


@router.post('/{dataset_id}/drafts', response={201: DraftResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 422: ErrorResponse, 503: ErrorResponse})
def create_draft_endpoint(request, dataset_id: UUID, payload: DraftCreateInput):
    """Create a creator-private draft at the branch's exact current tip."""

    try:
        dataset = _writable_dataset(request, dataset_id)
    except PermissionDenied:
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot write to this dataset.'})
    if dataset is None:
        return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
    try:
        return Status(201, _serialize_draft(create_browser_draft(dataset=dataset, user=request.auth, target_branch=payload.target_branch)))
    except (BrowserDraftInvalid, BrowserCommitUnavailable) as error:
        return _draft_error(error)


@router.get('/{dataset_id}/drafts', response={200: DraftListResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse})
def list_drafts_endpoint(request, dataset_id: UUID, target_branch: str | None = None):
    """List non-expired open drafts owned by the requesting creator."""

    try:
        dataset = _writable_dataset(request, dataset_id)
    except PermissionDenied:
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot write to this dataset.'})
    if dataset is None:
        return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
    drafts = BrowserCommitDraft.objects.filter(dataset=dataset, created_by=request.auth, state=BrowserCommitDraft.State.OPEN, expires_at__gt=timezone.now()).order_by('-updated_at', '-id')
    if target_branch is not None:
        drafts = drafts.filter(target_branch=target_branch)
    items = [_serialize_draft(draft) for draft in drafts[:100]]
    return {'count': len(items), 'items': items}


@router.get('/{dataset_id}/drafts/{draft_id}', response={200: DraftResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse})
def get_draft_endpoint(request, dataset_id: UUID, draft_id: UUID):
    """Return one creator-owned draft for review or resumption."""

    try:
        dataset = _writable_dataset(request, dataset_id)
    except PermissionDenied:
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot write to this dataset.'})
    if dataset is None:
        return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
    draft = _creator_draft(request, dataset, draft_id)
    return _serialize_draft(draft) if draft is not None else Status(404, {'code': 'draft_not_found', 'detail': 'The browser draft does not exist.'})


@router.delete('/{dataset_id}/drafts/{draft_id}', response={200: DraftResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 422: ErrorResponse})
def discard_draft_endpoint(request, dataset_id: UUID, draft_id: UUID):
    """Discard one creator-owned draft explicitly."""

    try:
        dataset = _writable_dataset(request, dataset_id)
    except PermissionDenied:
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot write to this dataset.'})
    if dataset is None:
        return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
    draft = _creator_draft(request, dataset, draft_id)
    if draft is None:
        return Status(404, {'code': 'draft_not_found', 'detail': 'The browser draft does not exist.'})
    try:
        return _serialize_draft(discard_browser_draft(draft=draft))
    except BrowserDraftInvalid as error:
        return _draft_error(error)


@router.put('/{dataset_id}/drafts/{draft_id}/files/git', response={200: DraftResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 413: ErrorResponse, 422: ErrorResponse, 503: ErrorResponse})
def stage_git_file_endpoint(request, dataset_id: UUID, draft_id: UUID, path: str, size: int = Query(ge=0)):
    """Stream one bounded ordinary file into Git without retaining a second copy."""

    try:
        dataset = _writable_dataset(request, dataset_id)
    except PermissionDenied:
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot write to this dataset.'})
    if dataset is None:
        return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
    if size > 10 * 1024 * 1024:
        return Status(413, {'code': 'git_blob_too_large', 'detail': 'Ordinary Git uploads must not exceed 10 MiB.'})
    if request.content_type != 'application/octet-stream':
        return Status(422, {'code': 'content_type_invalid', 'detail': 'Ordinary Git uploads require application/octet-stream.'})
    draft = _creator_draft(request, dataset, draft_id)
    if draft is None:
        return Status(404, {'code': 'draft_not_found', 'detail': 'The browser draft does not exist.'})
    try:
        stage_git_blob(draft=draft, path=path, size=size, stream=request)
        return _serialize_draft(BrowserCommitDraft.objects.get(pk=draft.pk))
    except (BrowserDraftInvalid, BrowserCommitUnavailable) as error:
        return _draft_error(error)


@router.post('/{dataset_id}/drafts/{draft_id}/files/lfs', response={200: LfsStageResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 422: ErrorResponse, 503: ErrorResponse})
def stage_lfs_file_endpoint(request, dataset_id: UUID, draft_id: UUID, payload: LfsStageInput):
    """Stage one LFS identity and authorize its direct object-storage transfer."""

    try:
        dataset = _writable_dataset(request, dataset_id)
    except PermissionDenied:
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot write to this dataset.'})
    if dataset is None:
        return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
    draft = _creator_draft(request, dataset, draft_id)
    if draft is None:
        return Status(404, {'code': 'draft_not_found', 'detail': 'The browser draft does not exist.'})
    try:
        change = stage_lfs_object(draft=draft, path=payload.path, oid=payload.oid, size=payload.size)
        lfs_object = change.lfs_object
        serialized_change = _serialize_change(change)
        if lfs_object.state in {LfsObject.State.AVAILABLE, LfsObject.State.REFERENCED}:
            return {'change': serialized_change, 'transfer': 'existing', 'upload': None, 'multipart': None}
        if payload.size >= settings.NIYAN_LFS_MULTIPART_THRESHOLD_BYTES:
            session = initiate_multipart_upload(lfs_object=lfs_object)
            return {'change': serialized_change, 'transfer': 'multipart', 'upload': None, 'multipart': {'session_id': session.id, 'part_size': session.part_size, 'part_count': session.expected_part_count}}
        action = issue_upload_action(lfs_object=lfs_object)
        return {'change': serialized_change, 'transfer': 'basic', 'upload': {'method': action.method, 'href': action.url, 'header': action.headers, 'expires_in': action.expires_in}, 'multipart': None}
    except (BrowserDraftInvalid, BrowserCommitUnavailable) as error:
        return _draft_error(error)
    except LfsIntegrityError:
        return Status(422, {'code': 'lfs_object_invalid', 'detail': 'The Git LFS object cannot be stored by the configured object storage.'})
    except (LfsTransferUnavailable, ObjectStoreError):
        return Status(503, {'code': 'object_storage_unavailable', 'detail': 'The direct upload is temporarily unavailable.'})


@router.post('/{dataset_id}/drafts/{draft_id}/files/lfs/{oid}/complete', response={200: DraftResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 422: ErrorResponse, 503: ErrorResponse})
def complete_lfs_file_endpoint(request, dataset_id: UUID, draft_id: UUID, oid: str):
    """Verify one direct basic upload and refresh its staged readiness."""

    try:
        dataset = _writable_dataset(request, dataset_id)
    except PermissionDenied:
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot write to this dataset.'})
    if dataset is None:
        return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
    draft = _creator_draft(request, dataset, draft_id)
    if draft is None:
        return Status(404, {'code': 'draft_not_found', 'detail': 'The browser draft does not exist.'})
    change = draft.changes.select_related('lfs_object').filter(storage='lfs', lfs_object__oid=oid).first()
    if change is None:
        return Status(404, {'code': 'draft_change_not_found', 'detail': 'The staged Git LFS file does not exist.'})
    try:
        finalize_lfs_upload(lfs_object=change.lfs_object)
        return _serialize_draft(BrowserCommitDraft.objects.get(pk=draft.pk))
    except LfsObjectMissing:
        return Status(404, {'code': 'lfs_object_not_found', 'detail': 'The uploaded Git LFS object is unavailable.'})
    except LfsIntegrityError:
        return Status(422, {'code': 'lfs_object_invalid', 'detail': 'The uploaded Git LFS object failed verification.'})
    except (LfsTransferUnavailable, ObjectStoreError):
        return Status(503, {'code': 'object_storage_unavailable', 'detail': 'Upload verification is temporarily unavailable.'})


@router.delete('/{dataset_id}/drafts/{draft_id}/files', response={200: DraftResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 422: ErrorResponse, 503: ErrorResponse})
def stage_delete_endpoint(request, dataset_id: UUID, draft_id: UUID, path: str):
    """Stage deletion of one existing regular file."""

    try:
        dataset = _writable_dataset(request, dataset_id)
    except PermissionDenied:
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot write to this dataset.'})
    if dataset is None:
        return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
    draft = _creator_draft(request, dataset, draft_id)
    if draft is None:
        return Status(404, {'code': 'draft_not_found', 'detail': 'The browser draft does not exist.'})
    try:
        stage_delete(draft=draft, path=path)
        return _serialize_draft(BrowserCommitDraft.objects.get(pk=draft.pk))
    except (BrowserDraftInvalid, BrowserCommitUnavailable) as error:
        return _draft_error(error)


@router.post('/{dataset_id}/drafts/{draft_id}/commit', response={200: DraftResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse, 422: ErrorResponse, 503: ErrorResponse})
def publish_draft_endpoint(request, dataset_id: UUID, draft_id: UUID, payload: DraftCommitInput):
    """Publish one reviewed draft with expected-old-object ref semantics."""

    try:
        dataset = _writable_dataset(request, dataset_id)
    except PermissionDenied:
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot write to this dataset.'})
    if dataset is None:
        return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
    draft = _creator_draft(request, dataset, draft_id)
    if draft is None:
        return Status(404, {'code': 'draft_not_found', 'detail': 'The browser draft does not exist.'})
    try:
        return _serialize_draft(publish_browser_draft(draft=draft, user=request.auth, message=payload.message))
    except (BrowserDraftInvalid, BrowserDraftConflict, BrowserDraftForbidden, BrowserCommitUnavailable) as error:
        record_audit_event(
            action='browser_draft.commit',
            outcome=AuditEvent.Outcome.REJECTED,
            reason_code=_draft_rejection_reason(error),
            actor=request.auth,
            access_token=get_access_token(request),
            scope=AuditEvent.Scope.DATASET,
            dataset=dataset,
            ref_name=f'refs/heads/{draft.target_branch}',
            draft_id=draft.id,
        )
        return _draft_error(error)
