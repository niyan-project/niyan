from datasets.policies import can_delete_group, can_manage_group, can_view_namespace
from namespaces.models import Namespace


def get_visible_namespace_by_path(*, namespace_path, user):
    """Resolve a namespace path only when the user may view it.

    Parameters
    ----------
    namespace_path : str
        Human-facing root or nested namespace path.
    user : accounts.models.User
        Authenticated user requesting the namespace.

    Returns
    -------
    namespaces.models.Namespace or None
        Visible namespace with its parent loaded, or ``None``.
    """

    path_parts = [part for part in namespace_path.strip('/').split('/') if part]
    if not path_parts:
        return None
    namespace = Namespace.objects.select_related('parent').filter(parent__isnull=True, slug=path_parts[0]).first()
    for namespace_slug in path_parts[1:]:
        if namespace is None:
            return None
        namespace = Namespace.objects.select_related('parent').filter(parent=namespace, slug=namespace_slug).first()
    if namespace is None or not can_view_namespace(user=user, namespace=namespace):
        return None
    return namespace


def get_visible_namespace(*, namespace_id, user):
    """Return one namespace by immutable identity when it is visible."""

    namespace = Namespace.objects.select_related('parent', 'owner_user').filter(pk=namespace_id).first()
    if namespace is None or not can_view_namespace(user=user, namespace=namespace):
        return None
    return namespace


def get_manageable_group(*, namespace_id, user):
    """Return one group only when the user has owner-level administration."""

    namespace = Namespace.objects.select_related('parent').filter(pk=namespace_id, kind=Namespace.Kind.GROUP).first()
    if namespace is None or not can_manage_group(user=user, namespace=namespace):
        return None
    return namespace


def get_deletable_group(*, namespace_id, user):
    """Return one group only when the user may permanently delete it."""

    namespace = Namespace.objects.select_related('parent').filter(pk=namespace_id, kind=Namespace.Kind.GROUP).first()
    if namespace is None or not can_delete_group(user=user, namespace=namespace):
        return None
    return namespace


def list_visible_namespaces(*, user):
    """Return visible personal and group namespaces in deterministic path order."""

    namespaces = Namespace.objects.select_related('parent', 'owner_user').order_by('slug', 'id')
    return sorted((namespace for namespace in namespaces if can_view_namespace(user=user, namespace=namespace)), key=lambda namespace: (namespace.path, str(namespace.id)))
