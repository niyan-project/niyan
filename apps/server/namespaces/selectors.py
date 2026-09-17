from datasets.policies import can_view_namespace
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
