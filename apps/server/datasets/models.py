import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q

from accounts.validators import normalize_path_slug, path_slug_validator
from namespaces.models import Namespace, NamespaceMembership


class Dataset(models.Model):
    """Identify a Git-backed dataset without duplicating repository history."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    namespace = models.ForeignKey(Namespace, on_delete=models.PROTECT, related_name='datasets')
    slug = models.CharField(max_length=100, validators=[path_slug_validator])
    name = models.CharField(max_length=255)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_datasets')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deletion_started_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        """Keep dataset paths unique within a namespace."""

        constraints = [
            models.UniqueConstraint(fields=['namespace', 'slug'], name='unique_dataset_slug_in_namespace'),
        ]

    def clean(self):
        """Normalize the mutable path before uniqueness validation."""

        super().clean()
        self.slug = normalize_path_slug(self.slug)

    def save(self, *args, **kwargs):
        """Persist the dataset with a normalized path slug.

        Parameters
        ----------
        *args
            Positional arguments passed to Django's model save operation.
        **kwargs
            Keyword arguments passed to Django's model save operation.
        """

        self.slug = normalize_path_slug(self.slug)
        return super().save(*args, **kwargs)

    @property
    def path(self):
        """Return the current human-facing dataset path."""

        return f'{self.namespace.path}/{self.slug}'

    def __str__(self):
        """Return the current dataset path for administrative displays."""

        return self.path


class DatasetGrant(models.Model):
    """Assign one Niyān role on a dataset to a user or group namespace."""

    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name='grants')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name='dataset_grants')
    group_namespace = models.ForeignKey(Namespace, null=True, blank=True, on_delete=models.CASCADE, related_name='dataset_grants')
    role = models.CharField(max_length=16, choices=NamespaceMembership.Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Require one principal and prevent duplicate dataset grants."""

        constraints = [
            models.CheckConstraint(
                condition=Q(user__isnull=False, group_namespace__isnull=True) | Q(user__isnull=True, group_namespace__isnull=False),
                name='dataset_grant_has_one_principal',
            ),
            models.UniqueConstraint(fields=['dataset', 'user'], condition=Q(user__isnull=False), name='unique_dataset_user_grant'),
            models.UniqueConstraint(fields=['dataset', 'group_namespace'], condition=Q(group_namespace__isnull=False), name='unique_dataset_group_grant'),
        ]

    def clean(self):
        """Validate principal shape and reject redundant owner-group grants."""

        super().clean()
        if (self.user_id is None) == (self.group_namespace_id is None):
            raise ValidationError('A dataset grant must target exactly one user or group namespace.')
        if self.group_namespace_id is not None:
            if self.group_namespace.kind != Namespace.Kind.GROUP:
                raise ValidationError({'group_namespace': 'Dataset group grants must target a Niyān group namespace.'})
            ancestor = self.dataset.namespace
            while ancestor is not None:
                if ancestor.pk == self.group_namespace_id:
                    raise ValidationError({'group_namespace': 'The dataset namespace already supplies access to this group.'})
                ancestor = ancestor.parent

    @property
    def principal_type(self):
        """Return the public principal discriminator."""

        return 'user' if self.user_id is not None else 'group'

    @property
    def principal_label(self):
        """Return the current username or group path."""

        return self.user.username if self.user_id is not None else self.group_namespace.path

    def __str__(self):
        """Return a concise grant description for administrative displays."""

        return f'{self.principal_label} as {self.role} on {self.dataset.path}'


class LfsObject(models.Model):
    """Track one dataset-scoped Git LFS object and its verified lifecycle."""

    class State(models.TextChoices):
        """Describe whether object content may satisfy repository operations."""

        PENDING = 'pending', 'Pending'
        AVAILABLE = 'available', 'Available'
        REFERENCED = 'referenced', 'Referenced'

    class VerificationMethod(models.TextChoices):
        """Describe the strongest storage evidence validated at finalization."""

        SIZE = 'size', 'Size only'
        SHA256 = 'sha256', 'SHA-256'
        CRC32C = 'crc32c', 'CRC32C'
        CRC32 = 'crc32', 'CRC32'

    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name='lfs_objects')
    oid = models.CharField(max_length=64, validators=[RegexValidator(regex=r'^[0-9a-f]{64}$', message='Use a lowercase 64-character SHA-256 object identifier.')])
    size = models.PositiveBigIntegerField()
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    verification_method = models.CharField(max_length=7, choices=VerificationMethod.choices, null=True, blank=True)
    verified_checksum = models.CharField(max_length=128, null=True, blank=True)
    available_at = models.DateTimeField(null=True, blank=True, editable=False)
    referenced_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Keep identity dataset-scoped and lifecycle evidence consistent."""

        constraints = [
            models.UniqueConstraint(fields=['dataset', 'oid'], name='unique_lfs_object_in_dataset'),
            models.CheckConstraint(
                condition=(
                    Q(state='pending', verification_method__isnull=True, available_at__isnull=True, referenced_at__isnull=True)
                    | Q(state='available', verification_method__isnull=False, available_at__isnull=False, referenced_at__isnull=True)
                    | Q(state='referenced', verification_method__isnull=False, available_at__isnull=False, referenced_at__isnull=False)
                ),
                name='lfs_object_state_timestamps_consistent',
            ),
            models.CheckConstraint(
                condition=(
                    Q(verification_method__isnull=True, verified_checksum__isnull=True)
                    | Q(verification_method='size', verified_checksum__isnull=True)
                    | Q(verification_method__in=['sha256', 'crc32c', 'crc32'], verified_checksum__isnull=False)
                ),
                name='lfs_object_verification_evidence_consistent',
            ),
        ]

    @property
    def storage_key(self):
        """Derive the private relative S3 key from immutable trusted identity."""

        return f'datasets/{self.dataset_id}/lfs/objects/{self.oid[:2]}/{self.oid[2:4]}/{self.oid}'

    def __str__(self):
        """Return a concise dataset-scoped object description."""

        return f'{self.dataset_id}:{self.oid}'
