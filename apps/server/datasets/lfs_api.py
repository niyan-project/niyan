from datetime import datetime
from typing import Literal
from uuid import UUID

from ninja import Field, Router, Schema, Status

from accounts.authentication import require_access, session_or_access_token
from datasets.lfs_transfers import LfsIntegrityError, LfsMultipartExpired, LfsTransferUnavailable, abort_multipart_upload, complete_multipart_upload, initiate_multipart_upload, issue_multipart_part_action
from datasets.models import Dataset, LfsMultipartUpload, LfsObject
from datasets.object_storage import CompletedPart, ObjectStoreError
from datasets.policies import can_write_repository


class MultipartInitiateInput(Schema):
    """Describe the object identity expected by multipart initiation."""

    size: int = Field(ge=0)


class MultipartSessionResponse(Schema):
    """Expose opaque session identity and the exact server-selected layout."""

    session_id: UUID
    oid: str
    size: int
    part_size: int
    part_count: int
    expires_at: datetime


class MultipartPartInput(Schema):
    """Describe the exact byte length of one requested part action."""

    size: int = Field(ge=1)


class MultipartPartActionResponse(Schema):
    """Expose one short-lived direct S3 part action."""

    method: Literal['PUT']
    href: str
    header: dict[str, str]
    expires_in: int


class MultipartCompletedPartInput(Schema):
    """Describe provider metadata returned after one direct part upload."""

    part_number: int = Field(ge=1, le=10_000)
    etag: str = Field(min_length=1, max_length=1024)
    checksum_sha256: str | None = Field(default=None, max_length=128)
    checksum_crc32c: str | None = Field(default=None, max_length=128)
    checksum_crc32: str | None = Field(default=None, max_length=128)


class MultipartCompleteInput(Schema):
    """Describe the ordered provider part metadata used for completion."""

    parts: list[MultipartCompletedPartInput] = Field(min_length=1, max_length=10_000)


class MultipartStateResponse(Schema):
    """Expose the terminal Niyān session state without provider identifiers."""

    session_id: UUID
    oid: str
    size: int
    state: Literal['completed', 'aborted']


class LfsControlErrorResponse(Schema):
    """Provide a stable multipart-control error without provider details."""

    code: str
    detail: str


router = Router(tags=['lfs'], auth=session_or_access_token)


@router.post('/{dataset_id}/lfs/objects/{oid}/multipart', response={200: MultipartSessionResponse, 401: LfsControlErrorResponse, 403: LfsControlErrorResponse, 404: LfsControlErrorResponse, 409: LfsControlErrorResponse, 422: LfsControlErrorResponse, 503: LfsControlErrorResponse})
def initiate_multipart_endpoint(request, dataset_id: UUID, oid: str, payload: MultipartInitiateInput):
    """Create or resume one authenticated opaque multipart session."""

    lfs_object = _get_writable_lfs_object(request=request, dataset_id=dataset_id, oid=oid)
    if lfs_object is None:
        return Status(404, {'code': 'lfs_object_not_found', 'detail': 'The Git LFS object is unavailable.'})
    if lfs_object.size != payload.size:
        return Status(422, {'code': 'lfs_size_mismatch', 'detail': 'The declared object size conflicts with existing metadata.'})
    try:
        session = initiate_multipart_upload(lfs_object=lfs_object)
    except LfsIntegrityError:
        return Status(422, {'code': 'lfs_object_invalid', 'detail': 'The Git LFS object cannot be stored through the configured S3 backend.'})
    except LfsTransferUnavailable:
        return Status(409, {'code': 'multipart_unavailable', 'detail': 'This Git LFS object does not accept multipart uploads.'})
    except ObjectStoreError:
        return Status(503, {'code': 'object_storage_unavailable', 'detail': 'Multipart initiation is temporarily unavailable.'})
    return _serialize_session(session)


@router.post('/{dataset_id}/lfs/objects/{oid}/multipart/{session_id}/parts/{part_number}', response={200: MultipartPartActionResponse, 401: LfsControlErrorResponse, 403: LfsControlErrorResponse, 404: LfsControlErrorResponse, 409: LfsControlErrorResponse, 422: LfsControlErrorResponse, 503: LfsControlErrorResponse})
def multipart_part_endpoint(request, dataset_id: UUID, oid: str, session_id: UUID, part_number: int, payload: MultipartPartInput):
    """Issue one exact-size direct upload action for a multipart part."""

    session = _get_writable_session(request=request, dataset_id=dataset_id, oid=oid, session_id=session_id)
    if session is None:
        return Status(404, {'code': 'multipart_not_found', 'detail': 'The multipart upload is unavailable.'})
    try:
        action = issue_multipart_part_action(session=session, part_number=part_number, size=payload.size)
    except LfsMultipartExpired:
        return Status(409, {'code': 'multipart_expired', 'detail': 'The multipart upload has expired.'})
    except (LfsIntegrityError, ValueError):
        return Status(422, {'code': 'multipart_part_invalid', 'detail': 'The multipart part does not match the server-selected layout.'})
    except LfsTransferUnavailable:
        return Status(409, {'code': 'multipart_unavailable', 'detail': 'The multipart upload is unavailable.'})
    except ObjectStoreError:
        return Status(503, {'code': 'object_storage_unavailable', 'detail': 'The multipart part action is temporarily unavailable.'})
    return {'method': action.method, 'href': action.url, 'header': action.headers, 'expires_in': action.expires_in}


@router.post('/{dataset_id}/lfs/objects/{oid}/multipart/{session_id}/complete', response={200: MultipartStateResponse, 401: LfsControlErrorResponse, 403: LfsControlErrorResponse, 404: LfsControlErrorResponse, 409: LfsControlErrorResponse, 422: LfsControlErrorResponse, 503: LfsControlErrorResponse})
def complete_multipart_endpoint(request, dataset_id: UUID, oid: str, session_id: UUID, payload: MultipartCompleteInput):
    """Complete provider assembly and finalize the Git LFS object."""

    session = _get_writable_session(request=request, dataset_id=dataset_id, oid=oid, session_id=session_id)
    if session is None:
        return Status(404, {'code': 'multipart_not_found', 'detail': 'The multipart upload is unavailable.'})
    try:
        parts = [
            CompletedPart(
                part_number=part.part_number,
                etag=part.etag,
                checksum_sha256=part.checksum_sha256,
                checksum_crc32c=part.checksum_crc32c,
                checksum_crc32=part.checksum_crc32,
            )
            for part in payload.parts
        ]
        completed = complete_multipart_upload(session=session, parts=parts)
    except LfsMultipartExpired:
        return Status(409, {'code': 'multipart_expired', 'detail': 'The multipart upload has expired.'})
    except (LfsIntegrityError, ValueError):
        return Status(422, {'code': 'multipart_parts_invalid', 'detail': 'The multipart completion metadata is invalid.'})
    except LfsTransferUnavailable:
        return Status(409, {'code': 'multipart_unavailable', 'detail': 'The multipart upload is unavailable.'})
    except ObjectStoreError:
        return Status(503, {'code': 'object_storage_unavailable', 'detail': 'Multipart completion is temporarily unavailable.'})
    return {'session_id': completed.id, 'oid': completed.lfs_object.oid, 'size': completed.lfs_object.size, 'state': completed.state}


@router.delete('/{dataset_id}/lfs/objects/{oid}/multipart/{session_id}', response={200: MultipartStateResponse, 401: LfsControlErrorResponse, 403: LfsControlErrorResponse, 404: LfsControlErrorResponse, 409: LfsControlErrorResponse, 503: LfsControlErrorResponse})
def abort_multipart_endpoint(request, dataset_id: UUID, oid: str, session_id: UUID):
    """Abort one provider upload idempotently after reauthorizing the dataset."""

    session = _get_writable_session(request=request, dataset_id=dataset_id, oid=oid, session_id=session_id)
    if session is None:
        return Status(404, {'code': 'multipart_not_found', 'detail': 'The multipart upload is unavailable.'})
    try:
        aborted = abort_multipart_upload(session=session)
    except LfsTransferUnavailable:
        return Status(409, {'code': 'multipart_unavailable', 'detail': 'The multipart upload cannot be aborted.'})
    except ObjectStoreError:
        return Status(503, {'code': 'object_storage_unavailable', 'detail': 'Multipart abort is temporarily unavailable.'})
    return {'session_id': aborted.id, 'oid': aborted.lfs_object.oid, 'size': aborted.lfs_object.size, 'state': aborted.state}


def _get_writable_lfs_object(*, request, dataset_id, oid):
    """Resolve one pending object after credential and current-role checks."""

    require_access(request=request, scope='write_repository', dataset_id=dataset_id)
    if len(oid) != 64 or any(character not in '0123456789abcdef' for character in oid):
        return None
    dataset = Dataset.objects.select_related('namespace__parent').filter(pk=dataset_id, deletion_started_at__isnull=True).first()
    if dataset is None or not can_write_repository(user=request.auth, dataset=dataset):
        return None
    return LfsObject.objects.select_related('dataset').filter(dataset=dataset, oid=oid).first()


def _get_writable_session(*, request, dataset_id, oid, session_id):
    """Resolve one opaque session without accepting provider identifiers."""

    lfs_object = _get_writable_lfs_object(request=request, dataset_id=dataset_id, oid=oid)
    if lfs_object is None:
        return None
    return LfsMultipartUpload.objects.select_related('lfs_object__dataset').filter(pk=session_id, lfs_object=lfs_object).first()


def _serialize_session(session):
    """Expose only client-required session layout and expiry metadata."""

    return {
        'session_id': session.id,
        'oid': session.lfs_object.oid,
        'size': session.lfs_object.size,
        'part_size': session.part_size,
        'part_count': session.expected_part_count,
        'expires_at': session.expires_at,
    }
