from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from accounts.validators import normalize_path_slug
from datasets.policies import can_create_group, can_manage_group
from namespaces.models import Namespace, NamespaceMembership


class GroupPathConflict(ValidationError):
    """Report that a requested group path component is already occupied."""


class GroupNotEmpty(ValidationError):
    """Report that a group still contains child groups or datasets."""


class LastGroupOwner(ValidationError):
    """Report an operation that would leave a group without a direct owner."""


def ensure_personal_namespace(user):
    """Return the one personal namespace belonging to a user, creating it when needed.

    Parameters
    ----------
    user : accounts.models.User
        Persisted Niyān user whose namespace is required.

    Returns
    -------
    namespaces.models.Namespace
        Existing or newly created personal namespace.

    Raises
    ------
    ValidationError
        If the user's normalized username conflicts with another root path.
    """

    existing_namespace = Namespace.objects.filter(owner_user=user).first()
    if existing_namespace is not None:
        return existing_namespace

    slug = normalize_path_slug(user.get_username())
    display_name = user.get_full_name().strip() or user.get_username()
    namespace = Namespace(kind=Namespace.Kind.PERSONAL, name=display_name, slug=slug, owner_user=user)
    namespace.full_clean()

    try:
        namespace.save()
    except IntegrityError as error:
        raise ValidationError({'username': 'This username conflicts with an existing root namespace path.'}) from error

    return namespace


def create_group(*, name, slug, created_by, parent=None):
    """Create a root or nested group and make the creator a direct owner.

    Parameters
    ----------
    name : str
        Human-facing group name.
    slug : str
        Path component beneath the optional parent.
    created_by : accounts.models.User
        Authenticated creator who becomes a direct owner.
    parent : namespaces.models.Namespace, optional
        Parent group for a nested group.

    Returns
    -------
    namespaces.models.Namespace
        Persisted group with stable identity.
    """

    normalized_slug = normalize_path_slug(slug)
    with transaction.atomic():
        locked_parent = None
        if parent is not None:
            locked_parent = Namespace.objects.select_for_update().get(pk=parent.pk)
        if not can_create_group(user=created_by, parent=locked_parent):
            raise PermissionDenied('You cannot create a group in this location.')
        if locked_parent is not None and (locked_parent.children.filter(slug=normalized_slug).exists() or locked_parent.datasets.filter(slug=normalized_slug).exists()):
            raise GroupPathConflict({'slug': 'This group path is already in use.'})
        if locked_parent is None and Namespace.objects.filter(parent__isnull=True, slug=normalized_slug).exists():
            raise GroupPathConflict({'slug': 'This group path is already in use.'})

        group = Namespace(kind=Namespace.Kind.GROUP, name=name, slug=normalized_slug, parent=locked_parent)
        group.full_clean()
        try:
            group.save()
            membership = NamespaceMembership(namespace=group, user=created_by, role=NamespaceMembership.Role.OWNER)
            membership.full_clean()
            membership.save()
        except IntegrityError as error:
            raise GroupPathConflict({'slug': 'This group path is already in use.'}) from error
        return group


def update_group(*, group, updated_by, name=None, slug=None):
    """Update a group's mutable name or path component after owner authorization."""

    with transaction.atomic():
        locked_group = Namespace.objects.select_for_update().select_related('parent').get(pk=group.pk, kind=Namespace.Kind.GROUP)
        if not can_manage_group(user=updated_by, namespace=locked_group):
            raise PermissionDenied('You cannot update this group.')
        if slug is not None:
            normalized_slug = normalize_path_slug(slug)
            siblings = Namespace.objects.exclude(pk=locked_group.pk).filter(parent=locked_group.parent, slug=normalized_slug)
            dataset_conflict = locked_group.parent is not None and locked_group.parent.datasets.filter(slug=normalized_slug).exists()
            if siblings.exists() or dataset_conflict:
                raise GroupPathConflict({'slug': 'This group path is already in use.'})
            locked_group.slug = normalized_slug
        if name is not None:
            locked_group.name = name
        locked_group.full_clean()
        try:
            locked_group.save()
        except IntegrityError as error:
            raise GroupPathConflict({'slug': 'This group path is already in use.'}) from error
        return locked_group


def delete_group(*, group, deleted_by):
    """Permanently delete an empty group after owner authorization."""

    with transaction.atomic():
        locked_group = Namespace.objects.select_for_update().get(pk=group.pk, kind=Namespace.Kind.GROUP)
        if not can_manage_group(user=deleted_by, namespace=locked_group):
            raise PermissionDenied('You cannot delete this group.')
        if locked_group.children.exists() or locked_group.datasets.exists():
            raise GroupNotEmpty('A group containing groups or datasets cannot be deleted.')
        locked_group.delete()


def create_group_membership(*, group, user, role, created_by):
    """Add one direct user membership to a group."""

    with transaction.atomic():
        locked_group = Namespace.objects.select_for_update().get(pk=group.pk, kind=Namespace.Kind.GROUP)
        if not can_manage_group(user=created_by, namespace=locked_group):
            raise PermissionDenied('You cannot manage this group.')
        membership = NamespaceMembership(namespace=locked_group, user=user, role=role)
        membership.full_clean(validate_constraints=False)
        membership.save()
        return membership


def update_group_membership(*, membership, role, updated_by):
    """Change a direct group membership while preserving a direct owner."""

    with transaction.atomic():
        locked_membership = NamespaceMembership.objects.select_for_update().select_related('namespace', 'user').get(pk=membership.pk)
        if not can_manage_group(user=updated_by, namespace=locked_membership.namespace):
            raise PermissionDenied('You cannot manage this group.')
        if locked_membership.role == NamespaceMembership.Role.OWNER and role != NamespaceMembership.Role.OWNER and not locked_membership.namespace.memberships.exclude(pk=locked_membership.pk).filter(role=NamespaceMembership.Role.OWNER).exists():
            raise LastGroupOwner('A group must retain at least one direct owner.')
        locked_membership.role = role
        locked_membership.full_clean()
        locked_membership.save(update_fields=['role', 'updated_at'])
        return locked_membership


def delete_group_membership(*, membership, deleted_by):
    """Remove a direct group membership while preserving a direct owner."""

    with transaction.atomic():
        locked_membership = NamespaceMembership.objects.select_for_update().select_related('namespace').get(pk=membership.pk)
        if not can_manage_group(user=deleted_by, namespace=locked_membership.namespace):
            raise PermissionDenied('You cannot manage this group.')
        if locked_membership.role == NamespaceMembership.Role.OWNER and not locked_membership.namespace.memberships.exclude(pk=locked_membership.pk).filter(role=NamespaceMembership.Role.OWNER).exists():
            raise LastGroupOwner('A group must retain at least one direct owner.')
        locked_membership.delete()
