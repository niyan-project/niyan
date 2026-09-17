from datasets.models import Dataset
from namespaces.models import Namespace


def get_visible_dataset(*, dataset_id, user):
    """Return a dataset only when it is visible to the requesting user.

    Parameters
    ----------
    dataset_id : uuid.UUID
        Immutable dataset identity.
    user : accounts.models.User
        Authenticated user requesting access.

    Returns
    -------
    datasets.models.Dataset or None
        Dataset with its namespace loaded, or ``None`` when absent or inaccessible.
    """

    return Dataset.objects.select_related('namespace').filter(pk=dataset_id, namespace__kind=Namespace.Kind.PERSONAL, namespace__owner_user=user, deletion_started_at__isnull=True).first()


def get_visible_dataset_by_path(*, dataset_path, user):
    """Return an owned personal dataset resolved from its current path.

    Parameters
    ----------
    dataset_path : str
        Human-facing ``namespace/dataset`` path.
    user : accounts.models.User
        Authenticated user requesting access.

    Returns
    -------
    datasets.models.Dataset or None
        Visible dataset with its namespace loaded, or ``None``.
    """

    try:
        namespace_slug, dataset_slug = dataset_path.split('/')
    except ValueError:
        return None
    return Dataset.objects.select_related('namespace').filter(
        namespace__kind=Namespace.Kind.PERSONAL,
        namespace__owner_user=user,
        namespace__slug=namespace_slug,
        slug=dataset_slug,
        deletion_started_at__isnull=True,
    ).first()


def get_deletable_dataset(*, dataset_id, user):
    """Return an owned dataset including one with deletion already in progress.

    Parameters
    ----------
    dataset_id : uuid.UUID
        Immutable dataset identity.
    user : accounts.models.User
        Authenticated user requesting permanent deletion.

    Returns
    -------
    datasets.models.Dataset or None
        Owned dataset, or ``None`` when absent or inaccessible.
    """

    return Dataset.objects.select_related('namespace').filter(pk=dataset_id, namespace__kind=Namespace.Kind.PERSONAL, namespace__owner_user=user).first()


def list_visible_namespace_datasets(*, namespace_id, user):
    """Return an ordered dataset query for a visible personal namespace.

    Parameters
    ----------
    namespace_id : uuid.UUID
        Immutable namespace identity.
    user : accounts.models.User
        Authenticated user requesting access.

    Returns
    -------
    django.db.models.QuerySet or None
        Ordered datasets, or ``None`` when the namespace is absent or inaccessible.
    """

    namespace = Namespace.objects.filter(pk=namespace_id, kind=Namespace.Kind.PERSONAL, owner_user=user).first()
    if namespace is None:
        return None
    return Dataset.objects.select_related('namespace').filter(namespace=namespace, deletion_started_at__isnull=True).order_by('created_at', 'id')
