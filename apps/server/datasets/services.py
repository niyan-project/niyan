from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from accounts.validators import normalize_path_slug
from datasets.models import Dataset
from datasets.repositories import GitRepositoryStore
from namespaces.models import Namespace


class DatasetPathConflict(ValidationError):
    """Report that a namespace path component is already in use."""


def create_dataset(*, namespace, slug, name, created_by, repository_store=None):
    """Create a private dataset and its bare Git repository as one logical operation.

    Parameters
    ----------
    namespace : namespaces.models.Namespace
        Personal namespace that will contain the dataset.
    slug : str
        Human-facing dataset path component.
    name : str
        Dataset display name.
    created_by : accounts.models.User
        Authenticated user requesting creation.
    repository_store : datasets.repositories.GitRepositoryStore, optional
        Repository boundary override for tests.

    Returns
    -------
    datasets.models.Dataset
        Persisted dataset whose bare repository is ready.

    Raises
    ------
    PermissionDenied
        If the user cannot create datasets in the namespace.
    DatasetPathConflict
        If the requested slug conflicts with a dataset or child namespace.
    RepositoryProvisioningError
        If Git cannot create the repository.
    """

    store = repository_store or GitRepositoryStore()
    normalized_slug = normalize_path_slug(slug)
    provisioned_repository = None

    try:
        # A durable block prevents callers from wrapping this filesystem operation in a wider transaction whose later rollback would orphan the repository.
        with transaction.atomic(durable=True):
            locked_namespace = Namespace.objects.select_for_update().get(pk=namespace.pk)
            if locked_namespace.kind != Namespace.Kind.PERSONAL or locked_namespace.owner_user_id != created_by.pk:
                raise PermissionDenied('You cannot create a dataset in this namespace.')

            if locked_namespace.children.filter(slug=normalized_slug).exists() or locked_namespace.datasets.filter(slug=normalized_slug).exists():
                raise DatasetPathConflict({'slug': 'This namespace path is already in use.'})

            dataset = Dataset(namespace=locked_namespace, slug=normalized_slug, name=name, created_by=created_by)
            dataset.full_clean()
            dataset.save()
            provisioned_repository = store.create(dataset.id)

        return dataset
    except Exception:
        if provisioned_repository is not None:
            provisioned_repository.rollback()
        raise
