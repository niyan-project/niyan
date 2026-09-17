import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
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
