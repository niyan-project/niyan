from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from datasets.lfs_cleanup import LfsCleanupResult, cleanup_lfs_orphans
from datasets.models import Dataset, LfsMultipartUpload, LfsObject
from datasets.object_storage import ObjectStoreError


class RecordingCleanupStore:
    """Record cleanup mutations without contacting object storage."""

    def __init__(self):
        """Initialize call records and optional failure keys."""

        self.calls = []
        self.fail_deletions = set()

    def abort_multipart(self, relative_key, *, upload_id):
        """Record one incomplete provider upload abort."""

        self.calls.append(('abort', relative_key, upload_id))

    def delete(self, relative_key):
        """Record one object deletion or raise a retryable failure."""

        self.calls.append(('delete', relative_key))
        if relative_key in self.fail_deletions:
            raise ObjectStoreError('private provider detail')


@override_settings(NIYAN_LFS_ORPHAN_GRACE_PERIOD_SECONDS=7 * 24 * 60 * 60)
class LfsCleanupTests(TestCase):
    """Verify bounded cleanup respects lifecycle, grace, and provider failures."""

    def setUp(self):
        """Create one dataset and deterministic cleanup boundary."""

        user = User.objects.create_user(username='researcher')
        self.dataset = Dataset.objects.create(namespace=user.personal_namespace, slug='images', name='Images', created_by=user)
        self.store = RecordingCleanupStore()
        self.now = timezone.now()
        self.old = self.now - timedelta(days=8)

    def create_object(self, oid_character, *, state=LfsObject.State.PENDING, old=True):
        """Create one lifecycle-consistent object with controllable age."""

        fields = {'dataset': self.dataset, 'oid': oid_character * 64, 'size': 12, 'state': state}
        if state in {LfsObject.State.AVAILABLE, LfsObject.State.REFERENCED}:
            fields.update(verification_method=LfsObject.VerificationMethod.SIZE, available_at=self.old if old else self.now)
        if state == LfsObject.State.REFERENCED:
            fields['referenced_at'] = self.old if old else self.now
        lfs_object = LfsObject.objects.create(**fields)
        if old:
            LfsObject.objects.filter(pk=lfs_object.pk).update(created_at=self.old)
            lfs_object.refresh_from_db()
        return lfs_object

    def test_cleanup_aborts_expired_sessions_and_deletes_only_old_orphans(self):
        """Preserve fresh and referenced content while reclaiming failed uploads."""

        stale_pending = self.create_object('a')
        stale_available = self.create_object('b', state=LfsObject.State.AVAILABLE)
        referenced = self.create_object('c', state=LfsObject.State.REFERENCED)
        fresh_pending = self.create_object('d', old=False)
        session_object = self.create_object('e')
        LfsMultipartUpload.objects.create(
            lfs_object=session_object,
            provider_upload_id='expired-upload',
            part_size=5 * 1024 * 1024,
            expires_at=self.now - timedelta(seconds=1),
        )

        result = cleanup_lfs_orphans(object_store=self.store, now=self.now)

        self.assertEqual(result, LfsCleanupResult(aborted_multipart_uploads=1, deleted_objects=3, failures=0))
        self.assertFalse(LfsObject.objects.filter(pk__in=[stale_pending.pk, stale_available.pk, session_object.pk]).exists())
        self.assertEqual(LfsObject.objects.filter(pk__in=[referenced.pk, fresh_pending.pk]).count(), 2)
        self.assertIn(('abort', session_object.storage_key, 'expired-upload'), self.store.calls)

    def test_storage_failure_keeps_metadata_for_retry(self):
        """Never erase authoritative metadata after a transient provider failure."""

        lfs_object = self.create_object('f', state=LfsObject.State.AVAILABLE)
        self.store.fail_deletions.add(lfs_object.storage_key)

        result = cleanup_lfs_orphans(object_store=self.store, now=self.now)

        self.assertEqual(result.failures, 1)
        self.assertTrue(LfsObject.objects.filter(pk=lfs_object.pk).exists())

    def test_one_shot_management_command_uses_same_reconciliation_service(self):
        """Provide explicit reconciliation without separate cron-only behavior."""

        output = StringIO()
        with patch('datasets.management.commands.run_lfs_maintenance.cleanup_lfs_orphans', return_value=LfsCleanupResult(1, 2, 0)) as cleanup:
            call_command('run_lfs_maintenance', '--once', '--batch-size', '25', stdout=output)

        cleanup.assert_called_once_with(batch_size=25)
        self.assertIn('aborted_multipart_uploads=1 deleted_objects=2 failures=0', output.getvalue())
