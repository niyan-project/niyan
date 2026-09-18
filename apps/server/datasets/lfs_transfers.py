import base64
import hmac

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from datasets.models import LfsObject
from datasets.object_storage import ObjectNotFound, S3ObjectStore


class LfsTransferUnavailable(RuntimeError):
    """Report that lifecycle state does not permit the requested transfer."""


class LfsObjectMissing(LfsTransferUnavailable):
    """Report that a negotiated upload has no stored object to finalize."""


class LfsIntegrityError(LfsTransferUnavailable):
    """Report that stored object metadata conflicts with Git LFS identity."""


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


def finalize_lfs_upload(*, lfs_object, object_store=None):
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
    if stored_object.checksum_sha256 is not None:
        if not hmac.compare_digest(stored_object.checksum_sha256, expected_sha256):
            raise LfsIntegrityError('The uploaded Git LFS object failed SHA-256 verification.')
        verification_method = LfsObject.VerificationMethod.SHA256
        verified_checksum = stored_object.checksum_sha256
    elif store.supports_sha256_checksums:
        raise LfsIntegrityError('Object storage did not return the required SHA-256 evidence.')
    else:
        verification_method = LfsObject.VerificationMethod.SIZE
        verified_checksum = None

    with transaction.atomic():
        try:
            locked_object = LfsObject.objects.select_for_update().select_related('dataset').get(pk=lfs_object.pk)
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
