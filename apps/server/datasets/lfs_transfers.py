import base64
import hmac

from django.conf import settings
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from datasets.models import LfsMultipartUpload, LfsObject
from datasets.object_storage import ObjectNotFound, ObjectStoreError, S3ObjectStore


class LfsTransferUnavailable(RuntimeError):
    """Report that lifecycle state does not permit the requested transfer."""


class LfsObjectMissing(LfsTransferUnavailable):
    """Report that a negotiated upload has no stored object to finalize."""


class LfsIntegrityError(LfsTransferUnavailable):
    """Report that stored object metadata conflicts with Git LFS identity."""


class LfsMultipartExpired(LfsTransferUnavailable):
    """Report that a multipart session no longer accepts control operations."""


def issue_upload_action(*, lfs_object, object_store=None, expires_in=None):
    """Issue one direct upload action for a pending Git LFS object.

    Parameters
    ----------
    lfs_object : datasets.models.LfsObject
        Persisted object identity selected through an authorized dataset.
    object_store : datasets.object_storage.ObjectStore, optional
        Storage boundary override for deterministic tests.
    expires_in : int, optional
        Explicit lifetime override in seconds.

    Returns
    -------
    datasets.object_storage.PresignedAction
        Object- and operation-scoped direct upload action.

    Raises
    ------
    LfsTransferUnavailable
        If the dataset is being deleted or the object is no longer pending.
    ValueError
        If the requested lifetime falls outside the accepted bound.
    """

    _require_active_dataset(lfs_object)
    if lfs_object.state != LfsObject.State.PENDING:
        raise LfsTransferUnavailable('This Git LFS object does not accept uploads.')
    store = object_store or S3ObjectStore(settings.NIYAN_S3_CONFIGURATION)
    checksum_sha256 = _expected_sha256(lfs_object.oid) if store.supports_sha256_checksums else None
    return store.presign_upload(lfs_object.storage_key, size=lfs_object.size, expires_in=_action_lifetime(expires_in), checksum_sha256=checksum_sha256)


def issue_download_action(*, lfs_object, object_store=None, expires_in=None):
    """Issue one direct download action for an available Git LFS object.

    Parameters
    ----------
    lfs_object : datasets.models.LfsObject
        Persisted object identity selected through an authorized dataset.
    object_store : datasets.object_storage.ObjectStore, optional
        Storage boundary override for deterministic tests.
    expires_in : int, optional
        Explicit lifetime override in seconds.

    Returns
    -------
    datasets.object_storage.PresignedAction
        Object- and operation-scoped direct download action.

    Raises
    ------
    LfsTransferUnavailable
        If the dataset is being deleted or the object is still pending.
    ValueError
        If the requested lifetime falls outside the accepted bound.
    """

    _require_active_dataset(lfs_object)
    if lfs_object.state not in {LfsObject.State.AVAILABLE, LfsObject.State.REFERENCED}:
        raise LfsTransferUnavailable('This Git LFS object is not available for download.')
    store = object_store or S3ObjectStore(settings.NIYAN_S3_CONFIGURATION)
    return store.presign_download(lfs_object.storage_key, expires_in=_action_lifetime(expires_in))


def finalize_lfs_upload(*, lfs_object, object_store=None, verify_provider_sha256=True):
    """Verify stored metadata and make one pending Git LFS object available.

    Parameters
    ----------
    lfs_object : datasets.models.LfsObject
        Pending object identity selected through an authorized dataset.
    object_store : datasets.object_storage.ObjectStore, optional
        Storage boundary override for deterministic tests.

    Returns
    -------
    datasets.models.LfsObject
        Locked lifecycle record after idempotent finalization.

    Raises
    ------
    LfsObjectMissing
        If the negotiated storage key does not exist.
    LfsIntegrityError
        If size or available provider SHA-256 evidence conflicts.
    LfsTransferUnavailable
        If the dataset is being deleted.
    ObjectStoreError
        If provider metadata cannot be read reliably.
    """

    _require_active_dataset(lfs_object)
    if lfs_object.state in {LfsObject.State.AVAILABLE, LfsObject.State.REFERENCED}:
        return lfs_object
    store = object_store or S3ObjectStore(settings.NIYAN_S3_CONFIGURATION)
    try:
        stored_object = store.head(lfs_object.storage_key)
    except ObjectNotFound as error:
        raise LfsObjectMissing('The uploaded Git LFS object is unavailable.') from error
    if stored_object.size != lfs_object.size:
        raise LfsIntegrityError('The uploaded Git LFS object has an unexpected size.')

    expected_sha256 = _expected_sha256(lfs_object.oid)
    if verify_provider_sha256 and stored_object.checksum_sha256 is not None:
        if not hmac.compare_digest(stored_object.checksum_sha256, expected_sha256):
            raise LfsIntegrityError('The uploaded Git LFS object failed SHA-256 verification.')
        verification_method = LfsObject.VerificationMethod.SHA256
        verified_checksum = stored_object.checksum_sha256
    elif verify_provider_sha256 and store.supports_sha256_checksums:
        raise LfsIntegrityError('Object storage did not return the required SHA-256 evidence.')
    else:
        verification_method = LfsObject.VerificationMethod.SIZE
        verified_checksum = None

    with transaction.atomic():
        try:
            locked_object = LfsObject.objects.select_for_update(of=('self',)).select_related('dataset').get(pk=lfs_object.pk)
        except LfsObject.DoesNotExist as error:
            raise LfsTransferUnavailable('This Git LFS object is unavailable.') from error
        if locked_object.dataset.deletion_started_at is not None:
            raise LfsTransferUnavailable('This dataset is unavailable.')
        if locked_object.state in {LfsObject.State.AVAILABLE, LfsObject.State.REFERENCED}:
            return locked_object
        locked_object.state = LfsObject.State.AVAILABLE
        locked_object.verification_method = verification_method
        locked_object.verified_checksum = verified_checksum
        locked_object.available_at = timezone.now()
        locked_object.full_clean()
        locked_object.save(update_fields=['state', 'verification_method', 'verified_checksum', 'available_at', 'updated_at'])
        return locked_object


def initiate_multipart_upload(*, lfs_object, object_store=None, now=None):
    """Create or reuse one active provider multipart upload session.

    Parameters
    ----------
    lfs_object : datasets.models.LfsObject
        Pending object selected through an authorized dataset.
    object_store : datasets.object_storage.ObjectStore, optional
        Storage boundary override for deterministic tests.
    now : datetime.datetime, optional
        Clock override for deterministic tests.

    Returns
    -------
    datasets.models.LfsMultipartUpload
        Opaque Niyān session containing private provider state.
    """

    store = object_store or S3ObjectStore(settings.NIYAN_S3_CONFIGURATION)
    current_time = now or timezone.now()
    with transaction.atomic():
        locked_object = LfsObject.objects.select_for_update(of=('self',)).select_related('dataset').get(pk=lfs_object.pk)
        _require_active_dataset(locked_object)
        if locked_object.state != LfsObject.State.PENDING:
            raise LfsTransferUnavailable('This Git LFS object does not accept multipart uploads.')
        if locked_object.size < settings.NIYAN_LFS_MULTIPART_THRESHOLD_BYTES:
            raise LfsTransferUnavailable('This Git LFS object does not require multipart upload.')
        if locked_object.size > 5 * 1024**4:
            raise LfsIntegrityError('The Git LFS object exceeds the supported S3 object size.')
        active_session = locked_object.multipart_uploads.select_for_update().filter(state=LfsMultipartUpload.State.ACTIVE).first()
        if active_session is not None and active_session.expires_at > current_time:
            return active_session
        if active_session is not None:
            try:
                store.abort_multipart(locked_object.storage_key, upload_id=active_session.provider_upload_id)
            except ObjectNotFound:
                pass
            active_session.state = LfsMultipartUpload.State.ABORTED
            active_session.aborted_at = current_time
            active_session.full_clean()
            active_session.save(update_fields=['state', 'aborted_at', 'updated_at'])

        part_size = _multipart_part_size(locked_object.size)
        provider_upload_id = store.initiate_multipart(locked_object.storage_key)
        try:
            return LfsMultipartUpload.objects.create(
                lfs_object=locked_object,
                provider_upload_id=provider_upload_id,
                part_size=part_size,
                expires_at=current_time + timedelta(seconds=settings.NIYAN_LFS_MULTIPART_SESSION_LIFETIME_SECONDS),
            )
        except Exception:
            store.abort_multipart(locked_object.storage_key, upload_id=provider_upload_id)
            raise


def issue_multipart_part_action(*, session, part_number, size, object_store=None, expires_in=None, now=None):
    """Issue one exact-size signed action for an active multipart part."""

    store = object_store or S3ObjectStore(settings.NIYAN_S3_CONFIGURATION)
    current_time = now or timezone.now()
    session = LfsMultipartUpload.objects.select_related('lfs_object__dataset').get(pk=session.pk)
    _require_active_multipart_session(session, current_time)
    expected_size = session.expected_part_size(part_number)
    if size != expected_size:
        raise LfsIntegrityError('The multipart part size does not match the server-selected layout.')
    return store.presign_upload_part(
        session.lfs_object.storage_key,
        upload_id=session.provider_upload_id,
        part_number=part_number,
        size=size,
        expires_in=_action_lifetime(expires_in),
    )


def complete_multipart_upload(*, session, parts, object_store=None, now=None):
    """Complete, verify, and publish one multipart upload idempotently."""

    store = object_store or S3ObjectStore(settings.NIYAN_S3_CONFIGURATION)
    current_time = now or timezone.now()
    with transaction.atomic():
        # Mutating services lock the object before its session so completion, abort, and replacement cannot deadlock or race provider state.
        locked_object = LfsObject.objects.select_for_update(of=('self',)).select_related('dataset').get(pk=session.lfs_object_id)
        locked_session = LfsMultipartUpload.objects.select_for_update().get(pk=session.pk)
        locked_session.lfs_object = locked_object
        if locked_session.state == LfsMultipartUpload.State.COMPLETED:
            return locked_session
        if locked_session.state == LfsMultipartUpload.State.ACTIVE and locked_object.state in {LfsObject.State.AVAILABLE, LfsObject.State.REFERENCED}:
            return _mark_multipart_completed(session=locked_session, current_time=current_time)
        _require_active_multipart_session(locked_session, current_time)
        expected_part_numbers = list(range(1, locked_session.expected_part_count + 1))
        if [part.part_number for part in parts] != expected_part_numbers:
            raise LfsIntegrityError('Multipart completion must contain every part exactly once in order.')

        try:
            store.complete_multipart(locked_object.storage_key, upload_id=locked_session.provider_upload_id, parts=parts)
        except ObjectStoreError as completion_error:
            # A previous request may have completed S3 successfully and failed before persisting Niyān state. Recover only if the canonical object can now be finalized.
            try:
                finalize_lfs_upload(lfs_object=locked_object, object_store=store, verify_provider_sha256=False)
            except (LfsTransferUnavailable, ObjectStoreError):
                raise completion_error
        else:
            finalize_lfs_upload(lfs_object=locked_object, object_store=store, verify_provider_sha256=False)
        return _mark_multipart_completed(session=locked_session, current_time=current_time)


def _mark_multipart_completed(*, session, current_time):
    """Persist terminal completion idempotently after object finalization."""

    with transaction.atomic():
        locked_session = LfsMultipartUpload.objects.select_for_update().get(pk=session.pk)
        if locked_session.state == LfsMultipartUpload.State.COMPLETED:
            return locked_session
        if locked_session.state != LfsMultipartUpload.State.ACTIVE:
            raise LfsTransferUnavailable('This multipart upload is unavailable.')
        locked_session.state = LfsMultipartUpload.State.COMPLETED
        locked_session.completed_at = current_time
        locked_session.full_clean()
        locked_session.save(update_fields=['state', 'completed_at', 'updated_at'])
        return locked_session


def abort_multipart_upload(*, session, object_store=None, now=None):
    """Abort one active provider upload and persist the terminal state."""

    store = object_store or S3ObjectStore(settings.NIYAN_S3_CONFIGURATION)
    current_time = now or timezone.now()
    with transaction.atomic():
        locked_object = LfsObject.objects.select_for_update(of=('self',)).select_related('dataset').get(pk=session.lfs_object_id)
        locked_session = LfsMultipartUpload.objects.select_for_update().get(pk=session.pk)
        locked_session.lfs_object = locked_object
        if locked_session.state == LfsMultipartUpload.State.ABORTED:
            return locked_session
        if locked_session.state == LfsMultipartUpload.State.COMPLETED:
            raise LfsTransferUnavailable('A completed multipart upload cannot be aborted.')
        try:
            store.abort_multipart(locked_session.lfs_object.storage_key, upload_id=locked_session.provider_upload_id)
        except ObjectNotFound:
            pass
        locked_session.state = LfsMultipartUpload.State.ABORTED
        locked_session.aborted_at = current_time
        locked_session.full_clean()
        locked_session.save(update_fields=['state', 'aborted_at', 'updated_at'])
        return locked_session


def _require_active_dataset(lfs_object):
    """Reject new transfer actions after irreversible deletion begins."""

    if lfs_object.dataset.deletion_started_at is not None:
        raise LfsTransferUnavailable('This dataset is unavailable.')


def _action_lifetime(expires_in):
    """Resolve and validate one configured or explicit action lifetime."""

    lifetime = settings.NIYAN_LFS_TRANSFER_ACTION_LIFETIME_SECONDS if expires_in is None else expires_in
    if lifetime < 1 or lifetime > 3600:
        raise ValueError('A signed action lifetime must be between 1 and 3600 seconds.')
    return lifetime


def _expected_sha256(oid):
    """Convert a lowercase Git LFS hex digest to S3's Base64 representation."""

    return base64.b64encode(bytes.fromhex(oid)).decode('ascii')


def _multipart_part_size(object_size):
    """Choose a configured part size that remains within S3's 10,000-part cap."""

    required_size = max(1, (object_size + 9_999) // 10_000)
    part_size = max(settings.NIYAN_LFS_MULTIPART_PART_SIZE_BYTES, required_size)
    if part_size > 5 * 1024 * 1024 * 1024:
        raise LfsIntegrityError('The Git LFS object exceeds the supported S3 multipart size.')
    return part_size


def _require_active_multipart_session(session, current_time):
    """Validate lifecycle, expiry, dataset, and object state for control calls."""

    _require_active_dataset(session.lfs_object)
    if session.state != LfsMultipartUpload.State.ACTIVE:
        raise LfsTransferUnavailable('This multipart upload is unavailable.')
    if session.expires_at <= current_time:
        raise LfsMultipartExpired('This multipart upload has expired.')
    if session.lfs_object.state != LfsObject.State.PENDING:
        raise LfsTransferUnavailable('This Git LFS object no longer accepts multipart data.')
