import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from accounts.validators import normalize_path_slug, path_slug_validator


class User(AbstractUser):
    """Represent a Niyān user with Django's standard authentication fields."""

    username = models.CharField(
        max_length=100,
        unique=True,
        validators=[path_slug_validator],
        error_messages={'unique': 'A user with that username already exists.'},
        help_text='Used for login and the initial personal namespace path.',
    )

    def clean(self):
        """Normalize the username before uniqueness validation."""

        super().clean()
        self.username = normalize_path_slug(self.username)

    def save(self, *args, **kwargs):
        """Persist the user with a normalized path-safe username.

        Parameters
        ----------
        *args
            Positional arguments passed to Django's model save operation.
        **kwargs
            Keyword arguments passed to Django's model save operation.
        """

        self.username = normalize_path_slug(self.username)
        path_slug_validator(self.username)
        return super().save(*args, **kwargs)


class AccessToken(models.Model):
    """Store one non-recoverable credential issued to a user."""

    class Origin(models.TextChoices):
        """Identify how a token was issued for audit displays."""

        MANUAL = 'manual', 'Manual'
        CLI = 'cli', 'CLI'

    class ResourceBoundary(models.TextChoices):
        """Limit a token to the user account or one dataset."""

        USER = 'user', 'User'
        DATASET = 'dataset', 'Dataset'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='access_tokens')
    name = models.CharField(max_length=255)
    origin = models.CharField(max_length=10, choices=Origin.choices)
    resource_boundary = models.CharField(max_length=10, choices=ResourceBoundary.choices)
    dataset = models.ForeignKey('datasets.Dataset', null=True, blank=True, on_delete=models.CASCADE, related_name='access_tokens')
    scopes = models.JSONField(default=list)
    selector = models.CharField(max_length=16, unique=True, editable=False)
    secret_digest = models.CharField(max_length=64, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True, editable=False)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        """Keep resource boundary data internally consistent."""

        ordering = ['-created_at', '-id']
        constraints = [
            models.CheckConstraint(
                condition=Q(resource_boundary='user', dataset__isnull=True) | Q(resource_boundary='dataset', dataset__isnull=False),
                name='access_token_resource_boundary_matches_dataset',
            ),
        ]

    def clean(self):
        """Validate the resource boundary and scope representation."""

        super().clean()
        if self.resource_boundary == self.ResourceBoundary.USER and self.dataset_id is not None:
            raise ValidationError({'dataset': 'User-level tokens cannot be bound to a dataset.'})
        if self.resource_boundary == self.ResourceBoundary.DATASET and self.dataset_id is None:
            raise ValidationError({'dataset': 'Dataset-level tokens require a dataset.'})
        if not isinstance(self.scopes, list) or not all(isinstance(scope, str) for scope in self.scopes):
            raise ValidationError({'scopes': 'Scopes must be a list of strings.'})

    def is_active(self, *, at=None):
        """Return whether this token may still authenticate.

        Parameters
        ----------
        at : datetime.datetime, optional
            Time at which to evaluate expiry.

        Returns
        -------
        bool
            Whether the token is neither revoked nor expired.
        """

        checked_at = at or timezone.now()
        return self.revoked_at is None and self.expires_at > checked_at

    @property
    def fingerprint(self):
        """Return a non-secret identifier suitable for account displays."""

        return f'niyan_{self.selector}_…'


class DeviceAuthorization(models.Model):
    """Track one short-lived browser-assisted CLI login request."""

    class Status(models.TextChoices):
        """Describe the browser decision and exchange state."""

        PENDING = 'pending', 'Pending'
        APPROVED = 'approved', 'Approved'
        DENIED = 'denied', 'Denied'
        CONSUMED = 'consumed', 'Consumed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device_secret_digest = models.CharField(max_length=64, editable=False)
    user_code = models.CharField(max_length=9, unique=True, editable=False)
    name = models.CharField(max_length=255)
    requested_scopes = models.JSONField(default=list)
    requested_dataset_path = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name='device_authorizations')
    dataset = models.ForeignKey('datasets.Dataset', null=True, blank=True, on_delete=models.CASCADE, related_name='device_authorizations')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    last_polled_at = models.DateTimeField(null=True, blank=True, editable=False)
    decided_at = models.DateTimeField(null=True, blank=True, editable=False)
    consumed_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        """Show the newest device login requests first."""

        ordering = ['-created_at', '-id']

    def is_expired(self, *, at=None):
        """Return whether the device request is past its exchange deadline.

        Parameters
        ----------
        at : datetime.datetime, optional
            Time at which to evaluate expiry.

        Returns
        -------
        bool
            Whether the authorization has expired.
        """

        return self.expires_at <= (at or timezone.now())
