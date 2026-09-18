import base64
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from datasets.lfs_transfers import LfsIntegrityError, LfsObjectMissing, LfsTransferUnavailable, finalize_lfs_upload, issue_download_action, issue_upload_action
from datasets.models import Dataset, LfsObject
from datasets.object_storage import ObjectNotFound, PresignedAction, StoredObject


class RecordingObjectStore:
    """Record signed-action inputs without implementing object transfer mechanics."""

    def __init__(self):
        """Initialize an empty call log."""

        self.calls = []
        self.supports_sha256_checksums = False
        self.stored_object = None

    def presign_upload(self, relative_key, *, size, expires_in, checksum_sha256=None):
        """Record and return a deterministic upload action."""

        self.calls.append(('upload', relative_key, size, expires_in, checksum_sha256))
        return PresignedAction(method='PUT', url='https://storage.example.test/signed-upload', headers={'Content-Length': str(size)}, expires_in=expires_in)

    def presign_download(self, relative_key, *, expires_in):
        """Record and return a deterministic download action."""

        self.calls.append(('download', relative_key, expires_in))
        return PresignedAction(method='GET', url='https://storage.example.test/signed-download', expires_in=expires_in)

    def head(self, relative_key):
        """Return configured metadata or a deterministic missing-object error."""

        self.calls.append(('head', relative_key))
        if self.stored_object is None:
            raise ObjectNotFound('missing')
        return self.stored_object


class LfsTransferActionTests(TestCase):
    """Verify lifecycle-scoped direct transfer action issuance."""

    def setUp(self):
        """Create one dataset and a recording object-store boundary."""

        user = User.objects.create_user(username='researcher')
        self.dataset = Dataset.objects.create(namespace=user.personal_namespace, slug='images', name='Images', created_by=user)
        self.object_store = RecordingObjectStore()

    @override_settings(NIYAN_LFS_TRANSFER_ACTION_LIFETIME_SECONDS=600)
    def test_upload_action_uses_trusted_key_size_and_configured_lifetime(self):
        """Bind a pending object's immutable identity into a direct PUT action."""

        lfs_object = LfsObject.objects.create(dataset=self.dataset, oid='a' * 64, size=12)

        action = issue_upload_action(lfs_object=lfs_object, object_store=self.object_store)

        self.assertEqual(action.method, 'PUT')
        self.assertEqual(action.expires_in, 600)
        self.assertNotIn(action.url, repr(action))
        self.assertEqual(
            self.object_store.calls,
            [('upload', f'datasets/{self.dataset.id}/lfs/objects/aa/aa/{lfs_object.oid}', 12, 600, None)],
        )

    def test_download_action_accepts_available_and_referenced_objects(self):
        """Issue GET actions only after transfer finalization succeeds."""

        now = timezone.now()
        objects = [
            LfsObject.objects.create(
                dataset=self.dataset,
                oid='b' * 64,
                size=12,
                state=LfsObject.State.AVAILABLE,
                verification_method=LfsObject.VerificationMethod.SIZE,
                available_at=now,
            ),
            LfsObject.objects.create(
                dataset=self.dataset,
                oid='c' * 64,
                size=24,
                state=LfsObject.State.REFERENCED,
                verification_method=LfsObject.VerificationMethod.CRC32C,
                verified_checksum='provider-checksum',
                available_at=now,
                referenced_at=now,
            ),
        ]

        actions = [issue_download_action(lfs_object=lfs_object, object_store=self.object_store, expires_in=300) for lfs_object in objects]

        self.assertEqual([action.method for action in actions], ['GET', 'GET'])
        self.assertEqual([call[0] for call in self.object_store.calls], ['download', 'download'])
        self.assertTrue(all(call[-1] == 300 for call in self.object_store.calls))

    def test_provider_sha256_capability_binds_expected_digest_into_upload(self):
        """Ask a capable provider to reject content that does not match the LFS OID."""

        lfs_object = LfsObject.objects.create(dataset=self.dataset, oid='a' * 64, size=12)
        expected_checksum = base64.b64encode(bytes.fromhex(lfs_object.oid)).decode('ascii')
        self.object_store.supports_sha256_checksums = True

        issue_upload_action(lfs_object=lfs_object, object_store=self.object_store)

        self.assertEqual(self.object_store.calls[-1][-1], expected_checksum)

    def test_finalization_records_exact_size_evidence_for_compatible_provider(self):
        """Record honest size-only evidence when optional SHA-256 is disabled."""

        lfs_object = LfsObject.objects.create(dataset=self.dataset, oid='3' * 64, size=12)
        self.object_store.stored_object = StoredObject(relative_key=lfs_object.storage_key, size=12)

        finalized = finalize_lfs_upload(lfs_object=lfs_object, object_store=self.object_store)

        self.assertEqual(finalized.state, LfsObject.State.AVAILABLE)
        self.assertEqual(finalized.verification_method, LfsObject.VerificationMethod.SIZE)
        self.assertIsNone(finalized.verified_checksum)
        self.assertIsNotNone(finalized.available_at)

    def test_finalization_validates_provider_sha256_evidence(self):
        """Promote only an exact SHA-256 match when the provider returns one."""

        lfs_object = LfsObject.objects.create(dataset=self.dataset, oid='4' * 64, size=12)
        expected_checksum = base64.b64encode(bytes.fromhex(lfs_object.oid)).decode('ascii')
        self.object_store.supports_sha256_checksums = True
        self.object_store.stored_object = StoredObject(relative_key=lfs_object.storage_key, size=12, checksum_sha256=expected_checksum)

        finalized = finalize_lfs_upload(lfs_object=lfs_object, object_store=self.object_store)

        self.assertEqual(finalized.verification_method, LfsObject.VerificationMethod.SHA256)
        self.assertEqual(finalized.verified_checksum, expected_checksum)

    def test_finalization_retry_reuses_available_state_without_storage_call(self):
        """Make a repeated verification request safe after successful promotion."""

        lfs_object = LfsObject.objects.create(dataset=self.dataset, oid='9' * 64, size=12)
        self.object_store.stored_object = StoredObject(relative_key=lfs_object.storage_key, size=12)
        finalized = finalize_lfs_upload(lfs_object=lfs_object, object_store=self.object_store)
        calls_after_first_finalization = list(self.object_store.calls)

        retried = finalize_lfs_upload(lfs_object=finalized, object_store=self.object_store)

        self.assertEqual(retried.state, LfsObject.State.AVAILABLE)
        self.assertEqual(self.object_store.calls, calls_after_first_finalization)

    def test_finalization_rejects_missing_size_or_sha256_evidence(self):
        """Leave pending metadata unavailable after any integrity failure."""

        cases = [
            (None, False, LfsObjectMissing),
            (StoredObject(relative_key='object', size=13), False, LfsIntegrityError),
            (StoredObject(relative_key='object', size=12, checksum_sha256='wrong'), True, LfsIntegrityError),
            (StoredObject(relative_key='object', size=12), True, LfsIntegrityError),
        ]
        for index, (stored_object, supports_sha256, expected_error) in enumerate(cases, start=5):
            with self.subTest(expected_error=expected_error):
                lfs_object = LfsObject.objects.create(dataset=self.dataset, oid=f'{index:x}' * 64, size=12)
                self.object_store.stored_object = stored_object
                self.object_store.supports_sha256_checksums = supports_sha256
                with self.assertRaises(expected_error):
                    finalize_lfs_upload(lfs_object=lfs_object, object_store=self.object_store)
                lfs_object.refresh_from_db()
                self.assertEqual(lfs_object.state, LfsObject.State.PENDING)

    def test_lifecycle_state_rejects_wrong_transfer_direction(self):
        """Prevent overwrite of finalized content and reads of pending content."""

        now = timezone.now()
        pending = LfsObject.objects.create(dataset=self.dataset, oid='d' * 64, size=12)
        available = LfsObject.objects.create(
            dataset=self.dataset,
            oid='e' * 64,
            size=12,
            state=LfsObject.State.AVAILABLE,
            verification_method=LfsObject.VerificationMethod.SIZE,
            available_at=now,
        )

        with self.assertRaises(LfsTransferUnavailable):
            issue_download_action(lfs_object=pending, object_store=self.object_store)
        with self.assertRaises(LfsTransferUnavailable):
            issue_upload_action(lfs_object=available, object_store=self.object_store)

        self.assertEqual(self.object_store.calls, [])

    def test_deleting_dataset_rejects_new_actions(self):
        """Stop issuing bearer actions as soon as irreversible deletion begins."""

        lfs_object = LfsObject.objects.create(dataset=self.dataset, oid='f' * 64, size=12)
        self.dataset.deletion_started_at = timezone.now()
        self.dataset.save(update_fields=['deletion_started_at'])

        with self.assertRaises(LfsTransferUnavailable):
            issue_upload_action(lfs_object=lfs_object, object_store=self.object_store)

        self.assertEqual(self.object_store.calls, [])

    def test_rejects_expiry_outside_adapter_bound(self):
        """Reject overlong or already-expired bearer actions before signing."""

        lfs_object = LfsObject.objects.create(dataset=self.dataset, oid='1' * 64, size=12)

        for expires_in in (0, 3601):
            with self.subTest(expires_in=expires_in):
                with self.assertRaises(ValueError):
                    issue_upload_action(lfs_object=lfs_object, object_store=self.object_store, expires_in=expires_in)

        self.assertEqual(self.object_store.calls, [])

    @patch('datasets.lfs_transfers.S3ObjectStore')
    def test_default_store_uses_validated_django_configuration(self, store_class):
        """Construct the production adapter lazily from typed settings."""

        lfs_object = LfsObject.objects.create(dataset=self.dataset, oid='2' * 64, size=12)
        store_class.return_value = self.object_store

        issue_upload_action(lfs_object=lfs_object)

        store_class.assert_called_once()
        self.assertIs(store_class.call_args.args[0], settings.NIYAN_S3_CONFIGURATION)
