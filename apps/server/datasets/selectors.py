from datasets.models import Dataset
from datasets.policies import can_delete_dataset, can_manage_dataset_grants, can_read_dataset, can_view_namespace
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

    dataset = Dataset.objects.select_related('namespace__parent').filter(pk=dataset_id, deletion_started_at__isnull=True).first()
    if dataset is None or not can_read_dataset(user=user, dataset=dataset):
        return None
    return dataset


def get_visible_dataset_by_path(*, dataset_path, user):
    """Return a visible dataset resolved from its current nested path.

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

    path_parts = [part for part in dataset_path.strip('/').split('/') if part]
    if len(path_parts) < 2:
        return None
    namespace = Namespace.objects.filter(parent__isnull=True, slug=path_parts[0]).first()
    for namespace_slug in path_parts[1:-1]:
        if namespace is None:
            return None
        namespace = Namespace.objects.filter(parent=namespace, slug=namespace_slug).first()
    if namespace is None:
        return None
    dataset = Dataset.objects.select_related('namespace__parent').filter(namespace=namespace, slug=path_parts[-1], deletion_started_at__isnull=True).first()
    if dataset is None or not can_read_dataset(user=user, dataset=dataset):
        return None
    return dataset


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

    dataset = Dataset.objects.select_related('namespace__parent').filter(pk=dataset_id).first()
    if dataset is None or not can_delete_dataset(user=user, dataset=dataset):
        return None
    return dataset


def get_grant_manageable_dataset(*, dataset_id, user):
    """Return a dataset only when the user may administer its grants.

    Parameters
    ----------
    dataset_id : uuid.UUID
        Immutable dataset identity.
    user : accounts.models.User
        Authenticated user requesting access administration.

    Returns
    -------
    datasets.models.Dataset or None
        Manageable dataset with its namespace loaded, or ``None``.
    """

    dataset = Dataset.objects.select_related('namespace__parent').filter(pk=dataset_id, deletion_started_at__isnull=True).first()
    if dataset is None or not can_manage_dataset_grants(user=user, dataset=dataset):
        return None
    return dataset


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

    namespace = Namespace.objects.filter(pk=namespace_id).first()
    if namespace is None or not can_view_namespace(user=user, namespace=namespace):
        return None
    candidate_datasets = Dataset.objects.select_related('namespace__parent').filter(namespace=namespace, deletion_started_at__isnull=True).order_by('created_at', 'id')
    visible_ids = [dataset.id for dataset in candidate_datasets if can_read_dataset(user=user, dataset=dataset)]
    return candidate_datasets.filter(pk__in=visible_ids)


def list_visible_datasets(*, user, boundary_dataset_id=None):
    """Return every dataset visible to a user in deterministic order.

    Parameters
    ----------
    user : accounts.models.User
        Authenticated user requesting the dataset list.
    boundary_dataset_id : uuid.UUID, optional
        Immutable token boundary restricting candidate rows before policy evaluation.

    Returns
    -------
    django.db.models.QuerySet
        Ordered visible datasets with their namespaces loaded.
    """

    candidate_datasets = Dataset.objects.select_related('namespace__parent').filter(deletion_started_at__isnull=True).order_by('created_at', 'id')
    if boundary_dataset_id is not None:
        candidate_datasets = candidate_datasets.filter(pk=boundary_dataset_id)
    visible_ids = [dataset.id for dataset in candidate_datasets if can_read_dataset(user=user, dataset=dataset)]
    return candidate_datasets.filter(pk__in=visible_ids)
