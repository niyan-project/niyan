from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from datasets.models import LfsMultipartUpload, LfsObject
from datasets.object_storage import ObjectNotFound, ObjectStoreError, S3ObjectStore


@dataclass(frozen=True)
class LfsCleanupResult:
    """Summarize one bounded maintenance reconciliation pass."""

    aborted_multipart_uploads: int = 0
    deleted_objects: int = 0
    failures: int = 0


def cleanup_lfs_orphans(*, object_store=None, now=None, batch_size=100):
    """Abort expired sessions and delete old unreferenced Git LFS objects.

    Parameters
    ----------
    object_store : datasets.object_storage.ObjectStore, optional
        Storage boundary override for deterministic tests.
    now : datetime.datetime, optional
        Clock override for deterministic tests.
    batch_size : int, optional
        Maximum sessions and objects considered in this pass.

    Returns
    -------
    LfsCleanupResult
        Successful mutations and retryable failures observed.

    Raises
    ------
    ValueError
        If the requested batch size is outside the maintenance bound.
    """

    if batch_size < 1 or batch_size > 1000:
        raise ValueError('The cleanup batch size must be between 1 and 1000.')
    store = object_store or S3ObjectStore(settings.NIYAN_S3_CONFIGURATION)
    current_time = now or timezone.now()
    grace_cutoff = current_time - timedelta(seconds=settings.NIYAN_LFS_ORPHAN_GRACE_PERIOD_SECONDS)
    aborted = 0
    deleted = 0
    failures = 0

    # A failed post-receive hook must not let cleanup delete content that Git made reachable successfully.
    from datasets.git_push import reconcile_git_pushes

    reconcile_git_pushes(now=current_time, batch_size=batch_size)

    expired_candidates = list(
        LfsMultipartUpload.objects.filter(state=LfsMultipartUpload.State.ACTIVE, expires_at__lte=current_time)
        .order_by('expires_at', 'id')
        .values_list('id', 'lfs_object_id')[:batch_size]
    )
    for session_id, lfs_object_id in expired_candidates:
        try:
            if _abort_expired_session(store=store, session_id=session_id, lfs_object_id=lfs_object_id, current_time=current_time):
                aborted += 1
        except ObjectStoreError:
            failures += 1

    orphan_candidates = list(
        LfsObject.objects.filter(
            Q(state=LfsObject.State.PENDING, created_at__lte=grace_cutoff)
            | Q(state=LfsObject.State.AVAILABLE, available_at__lte=grace_cutoff)
        )
        .order_by('created_at', 'id')
        .values_list('id', flat=True)[:batch_size]
    )
    for lfs_object_id in orphan_candidates:
        try:
            if _delete_orphan(store=store, lfs_object_id=lfs_object_id, grace_cutoff=grace_cutoff, current_time=current_time):
                deleted += 1
        except ObjectStoreError:
            failures += 1

    return LfsCleanupResult(aborted_multipart_uploads=aborted, deleted_objects=deleted, failures=failures)


def _abort_expired_session(*, store, session_id, lfs_object_id, current_time):
    """Claim and abort one session whose expiry remains eligible."""

    with transaction.atomic():
        lfs_object = _claim(LfsObject.objects.filter(pk=lfs_object_id))
        if lfs_object is None:
            return False
        session = _claim(LfsMultipartUpload.objects.filter(pk=session_id, lfs_object=lfs_object))
        if session is None or session.state != LfsMultipartUpload.State.ACTIVE or session.expires_at > current_time:
            return False
        try:
            store.abort_multipart(lfs_object.storage_key, upload_id=session.provider_upload_id)
        except ObjectNotFound:
            pass
        session.state = LfsMultipartUpload.State.ABORTED
        session.aborted_at = current_time
        session.full_clean()
        session.save(update_fields=['state', 'aborted_at', 'updated_at'])
        return True


def _delete_orphan(*, store, lfs_object_id, grace_cutoff, current_time):
    """Claim, recheck, and delete one old unreferenced object."""

    with transaction.atomic():
        lfs_object = _claim(LfsObject.objects.filter(pk=lfs_object_id))
        if lfs_object is None or lfs_object.state == LfsObject.State.REFERENCED:
            return False
        if lfs_object.state == LfsObject.State.PENDING and lfs_object.created_at > grace_cutoff:
            return False
        if lfs_object.state == LfsObject.State.AVAILABLE and (lfs_object.available_at is None or lfs_object.available_at > grace_cutoff):
            return False
        if lfs_object.multipart_uploads.filter(state=LfsMultipartUpload.State.ACTIVE).exists():
            return False
        if lfs_object.push_leases.filter(expires_at__gt=current_time).exists():
            return False
        try:
            store.delete(lfs_object.storage_key)
        except ObjectNotFound:
            pass
        lfs_object.delete()
        return True


def _claim(queryset):
    """Claim the first matching row, skipping work held by another PostgreSQL worker."""

    if connection.features.has_select_for_update_skip_locked:
        return queryset.select_for_update(skip_locked=True).first()
    return queryset.select_for_update().first()
