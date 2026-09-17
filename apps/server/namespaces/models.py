import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from accounts.validators import normalize_path_slug, path_slug_validator


class Namespace(models.Model):
    """Provide a stable identity and mutable human-facing path for datasets."""

    class Kind(models.TextChoices):
        """Identify whether a namespace represents a user or a Niyān group."""

        PERSONAL = 'personal', 'Personal'
        GROUP = 'group', 'Group'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    name = models.CharField(max_length=255)
    slug = models.CharField(max_length=100, validators=[path_slug_validator])
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='children')
    owner_user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='personal_namespace')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Define namespace identity constraints."""

        constraints = [
            models.CheckConstraint(
                condition=(Q(kind='personal', owner_user__isnull=False, parent__isnull=True) | Q(kind='group', owner_user__isnull=True)),
                name='namespace_kind_shape',
            ),
            models.UniqueConstraint(fields=['slug'], condition=Q(parent__isnull=True), name='unique_root_namespace_slug'),
            models.UniqueConstraint(fields=['parent', 'slug'], condition=Q(parent__isnull=False), name='unique_child_namespace_slug'),
        ]

    def clean(self):
        """Validate ownership and hierarchy rules that span related records.

        Raises
        ------
        ValidationError
            If the namespace shape or parent hierarchy is invalid.
        """

        super().clean()
        self.slug = normalize_path_slug(self.slug)

        if self.kind == self.Kind.PERSONAL:
            if self.owner_user_id is None:
                raise ValidationError({'owner_user': 'A personal namespace must belong to a user.'})
            if self.parent_id is not None:
                raise ValidationError({'parent': 'A personal namespace must be a root namespace.'})
        elif self.kind == self.Kind.GROUP and self.owner_user_id is not None:
            raise ValidationError({'owner_user': 'A group namespace cannot belong to one user.'})

        if self.parent_id is not None:
            if self.parent.kind != self.Kind.GROUP:
                raise ValidationError({'parent': 'Only group namespaces can contain child namespaces.'})

            ancestor = self.parent
            visited_namespace_ids = {self.pk}
            while ancestor is not None:
                if ancestor.pk in visited_namespace_ids:
                    raise ValidationError({'parent': 'A namespace cannot be its own ancestor.'})
                visited_namespace_ids.add(ancestor.pk)
                ancestor = ancestor.parent

    def save(self, *args, **kwargs):
        """Persist the namespace with a normalized path slug.

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
        """Return the current human-facing path derived from ancestor slugs."""

        if self.parent_id is None:
            return self.slug
        return f'{self.parent.path}/{self.slug}'

    def __str__(self):
        """Return the current namespace path for administrative displays."""

        return self.path


class NamespaceMembership(models.Model):
    """Assign a Niyān role to a user within a group namespace."""

    class Role(models.TextChoices):
        """Name the initial ordered vocabulary for namespace access roles."""

        OWNER = 'owner', 'Owner'
        MAINTAINER = 'maintainer', 'Maintainer'
        CONTRIBUTOR = 'contributor', 'Contributor'
        READER = 'reader', 'Reader'

    namespace = models.ForeignKey(Namespace, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='namespace_memberships')
    role = models.CharField(max_length=16, choices=Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Prevent duplicate user membership within one group namespace."""

        constraints = [
            models.UniqueConstraint(fields=['namespace', 'user'], name='unique_namespace_membership'),
        ]

    def clean(self):
        """Restrict memberships to Niyān group namespaces.

        Raises
        ------
        ValidationError
            If the selected namespace is not a group namespace.
        """

        super().clean()
        if self.namespace_id is not None and self.namespace.kind != Namespace.Kind.GROUP:
            raise ValidationError({'namespace': 'Memberships can only target group namespaces.'})

    def __str__(self):
        """Return a concise membership description for administrative displays."""

        return f'{self.user} in {self.namespace} as {self.role}'
