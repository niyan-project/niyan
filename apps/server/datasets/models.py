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
    description = models.CharField(max_length=500, blank=True)
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


class LfsMultipartUpload(models.Model):
    """Persist one opaque provider multipart session for a Git LFS object."""

    class State(models.TextChoices):
        """Describe whether the provider upload still accepts control operations."""

        ACTIVE = 'active', 'Active'
        COMPLETED = 'completed', 'Completed'
        ABORTED = 'aborted', 'Aborted'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lfs_object = models.ForeignKey(LfsObject, on_delete=models.CASCADE, related_name='multipart_uploads')
    provider_upload_id = models.TextField(editable=False)
    part_size = models.PositiveBigIntegerField(editable=False)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    expires_at = models.DateTimeField(editable=False)
    completed_at = models.DateTimeField(null=True, blank=True, editable=False)
    aborted_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Permit at most one active provider upload for an object."""

        constraints = [
            models.UniqueConstraint(fields=['lfs_object'], condition=Q(state='active'), name='unique_active_multipart_upload'),
            models.CheckConstraint(
                condition=(
                    Q(state='active', completed_at__isnull=True, aborted_at__isnull=True)
                    | Q(state='completed', completed_at__isnull=False, aborted_at__isnull=True)
                    | Q(state='aborted', completed_at__isnull=True, aborted_at__isnull=False)
                ),
                name='multipart_upload_state_timestamps_consistent',
            ),
        ]

    @property
    def expected_part_count(self):
        """Return the exact number of parts implied by size and part size."""

        return max(1, (self.lfs_object.size + self.part_size - 1) // self.part_size)

    def expected_part_size(self, part_number):
        """Return the required byte length for one numbered part.

        Parameters
        ----------
        part_number : int
            One-based S3 multipart part number.

        Returns
        -------
        int
            Exact byte length for the requested part.

        Raises
        ------
        ValueError
            If the part number lies outside this upload's layout.
        """

        if part_number < 1 or part_number > self.expected_part_count:
            raise ValueError('The multipart part number is outside this upload.')
        offset = (part_number - 1) * self.part_size
        return min(self.part_size, self.lfs_object.size - offset)

    def __str__(self):
        """Return the public session identity without provider credentials."""

        return str(self.id)


class GitPushContext(models.Model):
    """Bind one receive-pack execution to freshly authorized server state."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name='push_contexts')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='git_push_contexts')
    access_token = models.ForeignKey('accounts.AccessToken', on_delete=models.CASCADE, related_name='git_push_contexts')
    role = models.CharField(max_length=16, choices=NamespaceMembership.Role.choices)
    request_id = models.UUIDField(default=uuid.uuid4, editable=False)
    expires_at = models.DateTimeField(editable=False)
    consumed_at = models.DateTimeField(null=True, blank=True, editable=False)
    validated_at = models.DateTimeField(null=True, blank=True, editable=False)
    completed_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Keep receive lifecycle timestamps internally consistent."""

        constraints = [
            models.CheckConstraint(condition=Q(validated_at__isnull=True) | Q(consumed_at__isnull=False), name='git_push_validation_requires_consumption'),
            models.CheckConstraint(condition=Q(completed_at__isnull=True) | Q(validated_at__isnull=False), name='git_push_completion_requires_validation'),
        ]

    def __str__(self):
        """Return the opaque context identifier for administrative inspection."""

        return str(self.id)


class GitPushRef(models.Model):
    """Record one policy-validated ref proposal for post-receive reconciliation."""

    push_context = models.ForeignKey(GitPushContext, on_delete=models.CASCADE, related_name='ref_updates')
    ref_name = models.CharField(max_length=1024)
    old_oid = models.CharField(max_length=64)
    new_oid = models.CharField(max_length=64)
    accepted_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        """Store each ref once within a receive transaction."""

        constraints = [
            models.UniqueConstraint(fields=['push_context', 'ref_name'], name='unique_ref_in_git_push'),
        ]

    def __str__(self):
        """Return the ref name and opaque parent context."""

        return f'{self.push_context_id}:{self.ref_name}'


class GitPushLfsLease(models.Model):
    """Protect one verified LFS object while a validated ref update is pending."""

    push_ref = models.ForeignKey(GitPushRef, on_delete=models.CASCADE, related_name='lfs_leases')
    lfs_object = models.ForeignKey(LfsObject, on_delete=models.CASCADE, related_name='push_leases')
    expires_at = models.DateTimeField(editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Deduplicate object protection within each proposed ref update."""

        constraints = [
            models.UniqueConstraint(fields=['push_ref', 'lfs_object'], name='unique_lfs_lease_in_git_push_ref'),
        ]

    def __str__(self):
        """Return a non-secret operational lease description."""

        return f'{self.push_ref_id}:{self.lfs_object_id}'
