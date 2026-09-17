from django.conf import settings

from datasets.models import LfsObject
from datasets.object_storage import S3ObjectStore


class LfsTransferUnavailable(RuntimeError):
    """Report that lifecycle state does not permit the requested transfer."""


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
    return store.presign_upload(lfs_object.storage_key, size=lfs_object.size, expires_in=_action_lifetime(expires_in))


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
