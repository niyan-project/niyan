from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.validators import normalize_path_slug
from datasets.models import Dataset, DatasetGrant, ProtectedRefRule
from datasets.object_storage import ObjectStoreError, S3ObjectStore
from datasets.policies import can_create_dataset, can_delete_dataset, can_manage_dataset_grants, can_manage_protected_refs, can_update_dataset
from datasets.repositories import GitRepositoryStore
from namespaces.models import Namespace


class DatasetPathConflict(ValidationError):
    """Report that a namespace path component is already in use."""


class DatasetObjectDeletionError(RuntimeError):
    """Report that dataset-owned object storage could not be removed safely."""


def create_dataset(*, namespace, slug, name, created_by, description='', repository_store=None):
    """Create a private dataset and its bare Git repository as one logical operation.

    Parameters
    ----------
    namespace : namespaces.models.Namespace
        Personal namespace that will contain the dataset.
    slug : str
        Human-facing dataset path component.
    name : str
        Dataset display name.
    description : str, optional
        Short human-facing dataset summary.
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
            if not can_create_dataset(user=created_by, namespace=locked_namespace):
                raise PermissionDenied('You cannot create a dataset in this namespace.')

            if locked_namespace.children.filter(slug=normalized_slug).exists() or locked_namespace.datasets.filter(slug=normalized_slug).exists():
                raise DatasetPathConflict({'slug': 'This namespace path is already in use.'})

            dataset = Dataset(namespace=locked_namespace, slug=normalized_slug, name=name, description=description, created_by=created_by)
            dataset.full_clean()
            dataset.save()
            provisioned_repository = store.create(dataset.id)

        return dataset
    except Exception:
        if provisioned_repository is not None:
            provisioned_repository.rollback()
        raise


def update_dataset(*, dataset, updated_by, slug=None, name=None, description=None):
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
    description : str, optional
        Replacement short summary, including an empty string to clear it.

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
        if locked_dataset.deletion_started_at is not None or not can_update_dataset(user=updated_by, dataset=locked_dataset):
            raise PermissionDenied('You cannot update this dataset.')

        if slug is not None:
            normalized_slug = normalize_path_slug(slug)
            conflicting_dataset = namespace.datasets.exclude(pk=locked_dataset.pk).filter(slug=normalized_slug).exists()
            if namespace.children.filter(slug=normalized_slug).exists() or conflicting_dataset:
                raise DatasetPathConflict({'slug': 'This namespace path is already in use.'})
            locked_dataset.slug = normalized_slug

        if name is not None:
            locked_dataset.name = name

        if description is not None:
            locked_dataset.description = description

        locked_dataset.full_clean()
        locked_dataset.save()

    return locked_dataset


def delete_dataset(*, dataset, deleted_by, repository_store=None, object_store=None):
    """Permanently delete a dataset and its bare Git repository.

    Parameters
    ----------
    dataset : datasets.models.Dataset
        Dataset selected by immutable identity.
    deleted_by : accounts.models.User
        Authenticated user requesting permanent deletion.
    repository_store : datasets.repositories.GitRepositoryStore, optional
        Repository boundary override for tests.
    object_store : datasets.object_storage.ObjectStore, optional
        Object-storage boundary override for tests.

    Raises
    ------
    PermissionDenied
        If the user cannot delete the dataset.
    RepositoryDeletionError
        If the repository cannot be removed safely.
    DatasetObjectDeletionError
        If dataset-owned S3 objects or multipart uploads cannot be removed.
    """

    store = repository_store or GitRepositoryStore()
    # Persist the irreversible transition before touching storage so an interrupted request can be retried without exposing a half-deleted dataset.
    with transaction.atomic(durable=True):
        locked_dataset = Dataset.objects.select_for_update().select_related('namespace').get(pk=dataset.pk)
        namespace = locked_dataset.namespace
        if not can_delete_dataset(user=deleted_by, dataset=locked_dataset):
            raise PermissionDenied('You cannot delete this dataset.')

        deletion_already_started = locked_dataset.deletion_started_at is not None
        if not deletion_already_started:
            locked_dataset.deletion_started_at = timezone.now()
            locked_dataset.save(update_fields=['deletion_started_at', 'updated_at'])

    storage = object_store or S3ObjectStore(settings.NIYAN_S3_CONFIGURATION)
    try:
        storage.delete_prefix(f'datasets/{locked_dataset.id}')
    except ObjectStoreError as error:
        raise DatasetObjectDeletionError('Dataset object storage could not be deleted.') from error

    store.delete(locked_dataset.id, allow_missing=deletion_already_started)

    with transaction.atomic(durable=True):
        Dataset.objects.select_for_update().get(pk=locked_dataset.pk).delete()


def create_dataset_grant(*, dataset, role, granted_by, user=None, group_namespace=None):
    """Grant one dataset role to a user or Niyān group.

    Parameters
    ----------
    dataset : datasets.models.Dataset
        Dataset whose access list will change.
    role : str
        Role from ``NamespaceMembership.Role``.
    granted_by : accounts.models.User
        User requesting the access change.
    user : accounts.models.User, optional
        Direct user principal.
    group_namespace : namespaces.models.Namespace, optional
        Niyān group principal.

    Returns
    -------
    datasets.models.DatasetGrant
        Persisted grant with its principal loaded.

    Raises
    ------
    PermissionDenied
        If the actor cannot administer dataset grants.
    ValidationError
        If the principal or role is invalid.
    """

    with transaction.atomic():
        locked_dataset = Dataset.objects.select_for_update().select_related('namespace__parent').get(pk=dataset.pk)
        if not can_manage_dataset_grants(user=granted_by, dataset=locked_dataset):
            raise PermissionDenied('You cannot manage grants for this dataset.')
        grant = DatasetGrant(dataset=locked_dataset, user=user, group_namespace=group_namespace, role=role)
        # Let the database's conditional unique constraints arbitrate concurrent duplicate requests so callers receive a stable conflict response.
        grant.full_clean(validate_constraints=False)
        grant.save()
        return grant


def update_dataset_grant(*, grant, role, updated_by):
    """Change the role assigned by a dataset grant.

    Parameters
    ----------
    grant : datasets.models.DatasetGrant
        Existing grant selected by immutable identity.
    role : str
        Replacement role.
    updated_by : accounts.models.User
        User requesting the access change.

    Returns
    -------
    datasets.models.DatasetGrant
        Updated grant.

    Raises
    ------
    PermissionDenied
        If the actor cannot administer dataset grants.
    ValidationError
        If the replacement role is invalid.
    """

    with transaction.atomic():
        locked_grant = DatasetGrant.objects.select_for_update().select_related('dataset__namespace__parent', 'user', 'group_namespace').get(pk=grant.pk)
        if not can_manage_dataset_grants(user=updated_by, dataset=locked_grant.dataset):
            raise PermissionDenied('You cannot manage grants for this dataset.')
        locked_grant.role = role
        locked_grant.full_clean()
        locked_grant.save(update_fields=['role', 'updated_at'])
        return locked_grant


def delete_dataset_grant(*, grant, deleted_by):
    """Remove a dataset grant after rechecking owner policy.

    Parameters
    ----------
    grant : datasets.models.DatasetGrant
        Existing grant selected by identity.
    deleted_by : accounts.models.User
        User requesting removal.

    Raises
    ------
    PermissionDenied
        If the actor cannot administer dataset grants.
    """

    with transaction.atomic():
        locked_grant = DatasetGrant.objects.select_for_update().select_related('dataset__namespace__parent').get(pk=grant.pk)
        if not can_manage_dataset_grants(user=deleted_by, dataset=locked_grant.dataset):
            raise PermissionDenied('You cannot manage grants for this dataset.')
        locked_grant.delete()


def create_protected_ref_rule(*, dataset, kind, pattern, minimum_role, deletion_minimum_role, created_by):
    """Create one stricter branch or tag mutation rule."""

    with transaction.atomic():
        locked_dataset = Dataset.objects.select_for_update().select_related('namespace__parent').get(pk=dataset.pk)
        if not can_manage_protected_refs(user=created_by, dataset=locked_dataset):
            raise PermissionDenied('You cannot manage protected refs for this dataset.')
        rule = ProtectedRefRule(dataset=locked_dataset, kind=kind, pattern=pattern, minimum_role=minimum_role, deletion_minimum_role=deletion_minimum_role)
        rule.full_clean(validate_constraints=False)
        rule.save()
        return rule


def update_protected_ref_rule(*, rule, updated_by, kind=None, pattern=None, minimum_role=None, deletion_minimum_role=None):
    """Update selected fields of one protected-ref rule."""

    with transaction.atomic():
        locked_rule = ProtectedRefRule.objects.select_for_update().select_related('dataset__namespace__parent').get(pk=rule.pk)
        if not can_manage_protected_refs(user=updated_by, dataset=locked_rule.dataset):
            raise PermissionDenied('You cannot manage protected refs for this dataset.')
        for field_name, value in {'kind': kind, 'pattern': pattern, 'minimum_role': minimum_role, 'deletion_minimum_role': deletion_minimum_role}.items():
            if value is not None:
                setattr(locked_rule, field_name, value)
        locked_rule.full_clean(validate_constraints=False)
        locked_rule.save()
        return locked_rule


def delete_protected_ref_rule(*, rule, deleted_by):
    """Delete one protected-ref rule after rechecking current authority."""

    with transaction.atomic():
        locked_rule = ProtectedRefRule.objects.select_for_update().select_related('dataset__namespace__parent').get(pk=rule.pk)
        if not can_manage_protected_refs(user=deleted_by, dataset=locked_rule.dataset):
            raise PermissionDenied('You cannot manage protected refs for this dataset.')
        locked_rule.delete()
