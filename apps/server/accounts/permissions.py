SYSTEM_PERMISSIONS = {
    'users.view': 'accounts.view_user',
    'users.add': 'accounts.add_user',
    'groups.view': 'namespaces.view_namespace',
    'groups.add': 'namespaces.add_namespace',
    'groups.change': 'namespaces.change_namespace',
    'groups.delete': 'namespaces.delete_namespace',
    'datasets.view': 'datasets.view_dataset',
    'datasets.add': 'datasets.add_dataset',
    'datasets.change': 'datasets.change_dataset',
    'datasets.delete': 'datasets.delete_dataset',
}


def get_system_permissions(user):
    """Return product-facing system permissions granted to a staff user.

    Parameters
    ----------
    user : accounts.models.User
        Authenticated user whose Django permissions are evaluated.

    Returns
    -------
    list[str]
        Stable permission names suitable for API and navigation decisions.
    """

    if not user.is_authenticated or not user.is_staff:
        return []
    return [public_name for public_name, django_permission in SYSTEM_PERMISSIONS.items() if user.has_perm(django_permission)]


def has_system_permission(*, user, permission):
    """Return whether a staff user has one product-facing permission."""

    django_permission = SYSTEM_PERMISSIONS.get(permission)
    return django_permission is not None and user.is_authenticated and user.is_staff and user.has_perm(django_permission)
