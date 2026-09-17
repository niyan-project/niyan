import uuid

from django.conf import settings
from django.db import models

from accounts.validators import normalize_path_slug, path_slug_validator
from namespaces.models import Namespace


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
