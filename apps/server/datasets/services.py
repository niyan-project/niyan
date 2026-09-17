from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

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


def update_dataset(*, dataset, updated_by, slug=None, name=None):
    """Update a dataset's mutable human-facing details.

    Parameters
    ----------
    dataset : datasets.models.Dataset
        Dataset selected by immutable identity.
    updated_by : accounts.models.User
        Authenticated user requesting the change.
    slug : str, optional
        Replacement path component.
    name : str, optional
        Replacement display name.

    Returns
    -------
    datasets.models.Dataset
        Updated dataset with its namespace loaded.

    Raises
    ------
    PermissionDenied
        If the user cannot update the dataset.
    DatasetPathConflict
        If the replacement slug conflicts with a dataset or child namespace.
    ValidationError
        If a replacement value is invalid.
    """

    with transaction.atomic():
        locked_dataset = Dataset.objects.select_for_update().select_related('namespace').get(pk=dataset.pk)
        namespace = locked_dataset.namespace
        if locked_dataset.deletion_started_at is not None or namespace.kind != Namespace.Kind.PERSONAL or namespace.owner_user_id != updated_by.pk:
            raise PermissionDenied('You cannot update this dataset.')

        if slug is not None:
            normalized_slug = normalize_path_slug(slug)
            conflicting_dataset = namespace.datasets.exclude(pk=locked_dataset.pk).filter(slug=normalized_slug).exists()
            if namespace.children.filter(slug=normalized_slug).exists() or conflicting_dataset:
                raise DatasetPathConflict({'slug': 'This namespace path is already in use.'})
            locked_dataset.slug = normalized_slug

        if name is not None:
            locked_dataset.name = name

        locked_dataset.full_clean()
        locked_dataset.save()

    return locked_dataset


def delete_dataset(*, dataset, deleted_by, repository_store=None):
    """Permanently delete a dataset and its bare Git repository.

    Parameters
    ----------
    dataset : datasets.models.Dataset
        Dataset selected by immutable identity.
    deleted_by : accounts.models.User
        Authenticated user requesting permanent deletion.
    repository_store : datasets.repositories.GitRepositoryStore, optional
        Repository boundary override for tests.

    Raises
    ------
    PermissionDenied
        If the user cannot delete the dataset.
    RepositoryDeletionError
        If the repository cannot be removed safely.
    """

    store = repository_store or GitRepositoryStore()

    # Persist the irreversible transition before touching storage so an interrupted request can be retried without exposing a half-deleted dataset.
    with transaction.atomic(durable=True):
        locked_dataset = Dataset.objects.select_for_update().select_related('namespace').get(pk=dataset.pk)
        namespace = locked_dataset.namespace
        if namespace.kind != Namespace.Kind.PERSONAL or namespace.owner_user_id != deleted_by.pk:
            raise PermissionDenied('You cannot delete this dataset.')

        deletion_already_started = locked_dataset.deletion_started_at is not None
        if not deletion_already_started:
            locked_dataset.deletion_started_at = timezone.now()
            locked_dataset.save(update_fields=['deletion_started_at', 'updated_at'])

    store.delete(locked_dataset.id, allow_missing=deletion_already_started)

    with transaction.atomic(durable=True):
        Dataset.objects.select_for_update().get(pk=locked_dataset.pk).delete()
