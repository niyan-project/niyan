from datetime import timedelta

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from datasets.models import Dataset, LfsObject


class LfsObjectTests(TestCase):
    """Verify dataset-scoped Git LFS identity and lifecycle persistence."""

    def setUp(self):
        """Create two datasets without involving their repository storage."""

        self.user = User.objects.create_user(username='researcher')
        self.dataset = Dataset.objects.create(namespace=self.user.personal_namespace, slug='images', name='Images', created_by=self.user)
        self.other_dataset = Dataset.objects.create(namespace=self.user.personal_namespace, slug='measurements', name='Measurements', created_by=self.user)
        self.oid = 'a' * 64

    def test_identity_is_unique_within_dataset_but_not_across_datasets(self):
        """Isolate identical LFS content metadata by immutable dataset identity."""

        LfsObject.objects.create(dataset=self.dataset, oid=self.oid, size=12)
        LfsObject.objects.create(dataset=self.other_dataset, oid=self.oid, size=12)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LfsObject.objects.create(dataset=self.dataset, oid=self.oid, size=12)

    def test_validates_sha256_identifier_and_non_negative_size(self):
        """Reject identifiers and sizes outside the standard Git LFS contract."""

        invalid_objects = [
            LfsObject(dataset=self.dataset, oid='A' * 64, size=12),
            LfsObject(dataset=self.dataset, oid='a' * 63, size=12),
            LfsObject(dataset=self.dataset, oid=self.oid, size=-1),
        ]

        for lfs_object in invalid_objects:
            with self.subTest(oid=lfs_object.oid, size=lfs_object.size):
                with self.assertRaises(ValidationError):
                    lfs_object.full_clean()

    def test_storage_key_uses_immutable_dataset_id_and_oid(self):
        """Keep mutable namespace and dataset paths out of private storage keys."""

        lfs_object = LfsObject.objects.create(dataset=self.dataset, oid=self.oid, size=12)
        original_key = lfs_object.storage_key
        self.dataset.slug = 'renamed-images'
        self.dataset.save()
        self.user.personal_namespace.slug = 'renamed-researcher'
        self.user.personal_namespace.save()

        self.assertEqual(original_key, f'datasets/{self.dataset.id}/lfs/objects/aa/aa/{self.oid}')
        self.assertEqual(lfs_object.storage_key, original_key)
        self.assertNotIn('images', original_key)
        self.assertNotIn('researcher', original_key)

    def test_accepts_consistent_lifecycle_states(self):
        """Persist pending, available, and referenced lifecycle evidence."""

        available_at = timezone.now()
        referenced_at = available_at + timedelta(seconds=1)
        objects = [
            LfsObject(dataset=self.dataset, oid='a' * 64, size=1),
            LfsObject(
                dataset=self.dataset,
                oid='b' * 64,
                size=2,
                state=LfsObject.State.AVAILABLE,
                verification_method=LfsObject.VerificationMethod.SIZE,
                available_at=available_at,
            ),
            LfsObject(
                dataset=self.dataset,
                oid='c' * 64,
                size=3,
                state=LfsObject.State.REFERENCED,
                verification_method=LfsObject.VerificationMethod.CRC32C,
                verified_checksum='provider-checksum',
                available_at=available_at,
                referenced_at=referenced_at,
            ),
        ]

        for lfs_object in objects:
            with self.subTest(state=lfs_object.state):
                lfs_object.full_clean()
                lfs_object.save()

    def test_rejects_inconsistent_lifecycle_or_verification_evidence(self):
        """Prevent unverified content from appearing available or referenced."""

        now = timezone.now()
        invalid_objects = [
            LfsObject(dataset=self.dataset, oid='b' * 64, size=1, state=LfsObject.State.AVAILABLE),
            LfsObject(
                dataset=self.dataset,
                oid='c' * 64,
                size=1,
                state=LfsObject.State.AVAILABLE,
                verification_method=LfsObject.VerificationMethod.SIZE,
                available_at=now,
                referenced_at=now,
            ),
            LfsObject(
                dataset=self.dataset,
                oid='d' * 64,
                size=1,
                state=LfsObject.State.REFERENCED,
                verification_method=LfsObject.VerificationMethod.SHA256,
                available_at=now,
                referenced_at=now,
            ),
            LfsObject(
                dataset=self.dataset,
                oid='e' * 64,
                size=1,
                state=LfsObject.State.AVAILABLE,
                verification_method=LfsObject.VerificationMethod.SIZE,
                verified_checksum='unexpected-checksum',
                available_at=now,
            ),
        ]

        for lfs_object in invalid_objects:
            with self.subTest(oid=lfs_object.oid):
                with self.assertRaises(ValidationError):
                    lfs_object.full_clean()

    def test_admin_exposes_read_only_operational_metadata(self):
        """Register lifecycle state for inspection without mutation shortcuts."""

        model_admin = admin.site._registry[LfsObject]

        self.assertIn('storage_key', model_admin.readonly_fields)
        self.assertFalse(model_admin.has_add_permission(request=None))
        self.assertFalse(model_admin.has_delete_permission(request=None))
