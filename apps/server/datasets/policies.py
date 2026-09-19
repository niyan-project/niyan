from fnmatch import fnmatchcase

from django.db.models import Q

from namespaces.models import Namespace, NamespaceMembership


ROLE_LEVELS = {
    NamespaceMembership.Role.READER: 10,
    NamespaceMembership.Role.CONTRIBUTOR: 20,
    NamespaceMembership.Role.MAINTAINER: 30,
    NamespaceMembership.Role.OWNER: 40,
}


def get_namespace_role(*, user, namespace):
    """Return a user's highest role inherited into a namespace.

    Parameters
    ----------
    user : accounts.models.User
        User whose current group memberships are evaluated.
    namespace : namespaces.models.Namespace
        Personal or group namespace being authorized.

    Returns
    -------
    str or None
        Highest applicable role, or ``None`` without access.
    """

    if user.is_superuser:
        return NamespaceMembership.Role.OWNER
    if namespace.kind == Namespace.Kind.PERSONAL:
        return NamespaceMembership.Role.OWNER if namespace.owner_user_id == user.pk else None

    roles = []
    ancestor = namespace
    while ancestor is not None:
        membership = ancestor.memberships.filter(user=user).only('role').first()
        if membership is not None:
            roles.append(membership.role)
        ancestor = ancestor.parent
    return _highest_role(roles)


def get_dataset_role(*, user, dataset):
    """Return a user's highest effective role for a dataset.

    Parameters
    ----------
    user : accounts.models.User
        User whose namespace memberships and grants are evaluated.
    dataset : datasets.models.Dataset
        Dataset being authorized.

    Returns
    -------
    str or None
        Highest effective role, or ``None`` without access.
    """

    if user.is_superuser:
        return NamespaceMembership.Role.OWNER

    roles = []
    namespace_role = get_namespace_role(user=user, namespace=dataset.namespace)
    if namespace_role is not None:
        roles.append(namespace_role)

    grants = dataset.grants.select_related('group_namespace__parent', 'user').filter(Q(user=user) | Q(group_namespace__isnull=False))
    for grant in grants:
        if grant.user_id == user.pk:
            roles.append(grant.role)
        elif grant.group_namespace_id is not None and grant.group_namespace.kind == Namespace.Kind.GROUP:
            membership_role = get_namespace_role(user=user, namespace=grant.group_namespace)
            if membership_role is not None:
                roles.append(_lower_role(membership_role, grant.role))
    return _highest_role(roles)


def can_view_namespace(*, user, namespace):
    """Return whether a namespace is visible to a user."""

    if get_namespace_role(user=user, namespace=namespace) is not None:
        return True
    return any(can_read_dataset(user=user, dataset=dataset) for dataset in namespace.datasets.select_related('namespace__parent').all())


def can_create_dataset(*, user, namespace):
    """Return whether a user may create datasets in a namespace."""

    return _at_least(get_namespace_role(user=user, namespace=namespace), NamespaceMembership.Role.MAINTAINER)


def can_create_group(*, user, parent=None):
    """Return whether a user may create a root or nested Niyān group."""

    if not user.is_authenticated:
        return False
    if parent is None:
        return True
    return _at_least(get_namespace_role(user=user, namespace=parent), NamespaceMembership.Role.OWNER)


def can_manage_group(*, user, namespace):
    """Return whether a user may update a group and its direct members."""

    return namespace.kind == Namespace.Kind.GROUP and _at_least(get_namespace_role(user=user, namespace=namespace), NamespaceMembership.Role.OWNER)


def can_read_dataset(*, user, dataset):
    """Return whether a user may view metadata and repository content."""

    return _at_least(get_dataset_role(user=user, dataset=dataset), NamespaceMembership.Role.READER)


def can_write_repository(*, user, dataset):
    """Return whether a user may push ordinary repository updates."""

    return _at_least(get_dataset_role(user=user, dataset=dataset), NamespaceMembership.Role.CONTRIBUTOR)


def can_update_dataset(*, user, dataset):
    """Return whether a user may change dataset metadata."""

    return _at_least(get_dataset_role(user=user, dataset=dataset), NamespaceMembership.Role.MAINTAINER)


def can_manage_dataset_grants(*, user, dataset):
    """Return whether a user may administer dataset principals."""

    return _at_least(get_dataset_role(user=user, dataset=dataset), NamespaceMembership.Role.OWNER)


def can_manage_protected_refs(*, user, dataset):
    """Return whether a user may configure stricter ref-mutation rules."""

    return _at_least(get_dataset_role(user=user, dataset=dataset), NamespaceMembership.Role.MAINTAINER)


def required_protected_ref_role(*, rules, kind, short_name, deleting):
    """Return the strictest role required by matching optional ref rules."""

    matching_roles = [rule.deletion_minimum_role if deleting else rule.minimum_role for rule in rules if rule.kind == kind and fnmatchcase(short_name, rule.pattern)]
    return max(matching_roles, key=ROLE_LEVELS.__getitem__, default=None)


def can_delete_dataset(*, user, dataset):
    """Return whether a user may permanently delete a dataset."""

    return _at_least(get_dataset_role(user=user, dataset=dataset), NamespaceMembership.Role.OWNER)


def _at_least(role, required_role):
    """Compare two roles using the accepted authorization ordering."""

    return role is not None and ROLE_LEVELS[role] >= ROLE_LEVELS[required_role]


def _highest_role(roles):
    """Return the highest role from an iterable, or ``None`` when empty."""

    return max(roles, key=ROLE_LEVELS.__getitem__, default=None)


def _lower_role(first_role, second_role):
    """Cap a group grant by the member's role within that group."""

    return min((first_role, second_role), key=ROLE_LEVELS.__getitem__)
