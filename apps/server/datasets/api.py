from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.urls import reverse
from ninja import Field, Query, Router, Schema, Status
from ninja.errors import AuthorizationError
from pydantic import model_validator

from accounts.authentication import get_access_token, require_access, session_or_access_token
from accounts.models import AccessToken
from datasets.repositories import RepositoryDeletionError, RepositoryProvisioningError
from datasets.models import DatasetGrant
from datasets.selectors import get_deletable_dataset, get_grant_manageable_dataset, get_visible_dataset, get_visible_dataset_by_path, list_visible_namespace_datasets
from datasets.services import DatasetPathConflict, create_dataset, create_dataset_grant, delete_dataset, delete_dataset_grant, update_dataset, update_dataset_grant
from namespaces.models import Namespace


class DatasetCreateInput(Schema):
    """Describe the public request for creating a dataset."""

    namespace_id: UUID
    slug: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255)


class DatasetResponse(Schema):
    """Expose a dataset's stable identity and current human-facing path."""

    id: UUID
    namespace_id: UUID
    namespace_path: str
    slug: str
    name: str
    created_at: datetime


class DatasetListResponse(Schema):
    """Describe one limit-and-offset page of visible datasets."""

    count: int
    limit: int
    offset: int
    items: list[DatasetResponse]


class DatasetRepositoryLocationResponse(Schema):
    """Resolve a mutable dataset path to immutable repository identity."""

    id: UUID
    path: str
    name: str
    git_url: str


class DatasetUpdateInput(Schema):
    """Describe mutable dataset fields accepted by a partial update."""

    slug: str | None = Field(default=None, min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode='after')
    def require_non_null_change(self) -> Self:
        """Reject empty updates and explicit null values.

        Returns
        -------
        DatasetUpdateInput
            Validated partial update.

        Raises
        ------
        ValueError
            If no field changes or a requested field is null.
        """

        if not self.model_fields_set or any(getattr(self, field_name) is None for field_name in self.model_fields_set):
            raise ValueError('At least one non-null field is required.')
        return self


class ErrorResponse(Schema):
    """Provide a stable, implementation-neutral client error."""

    code: str
    detail: str


class DatasetGrantCreateInput(Schema):
    """Describe a user or Niyān-group dataset grant."""

    user_id: int | None = None
    group_namespace_id: UUID | None = None
    role: Literal['reader', 'contributor', 'maintainer', 'owner']

    @model_validator(mode='after')
    def require_one_principal(self) -> Self:
        """Require exactly one grant principal.

        Returns
        -------
        DatasetGrantCreateInput
            Validated grant request.

        Raises
        ------
        ValueError
            If both or neither principal fields are selected.
        """

        if (self.user_id is None) == (self.group_namespace_id is None):
            raise ValueError('Select exactly one dataset grant principal.')
        return self


class DatasetGrantUpdateInput(Schema):
    """Describe a dataset grant role change."""

    role: Literal['reader', 'contributor', 'maintainer', 'owner']


class DatasetGrantResponse(Schema):
    """Expose one dataset grant without conflating principal types."""

    id: int
    dataset_id: UUID
    principal_type: str
    principal_label: str
    user_id: int | None
    group_namespace_id: UUID | None
    role: str
    created_at: datetime
    updated_at: datetime


class DatasetGrantListResponse(Schema):
    """Return the complete explicit grant list for a dataset owner."""

    count: int
    items: list[DatasetGrantResponse]


router = Router(tags=['datasets'], auth=session_or_access_token)


def serialize_dataset(dataset):
    """Convert a dataset model into its explicit public representation.

    Parameters
    ----------
    dataset : datasets.models.Dataset
        Dataset whose namespace has been loaded.

    Returns
    -------
    dict
        Fields allowed by the public API contract.
    """

    return {
        'id': dataset.id,
        'namespace_id': dataset.namespace_id,
        'namespace_path': dataset.namespace.path,
        'slug': dataset.slug,
        'name': dataset.name,
        'created_at': dataset.created_at,
    }


def serialize_dataset_grant(grant):
    """Convert a dataset grant into its explicit public representation.

    Parameters
    ----------
    grant : datasets.models.DatasetGrant
        Grant whose principal relations have been loaded.

    Returns
    -------
    dict
        Public dataset grant fields.
    """

    return {
        'id': grant.id,
        'dataset_id': grant.dataset_id,
        'principal_type': grant.principal_type,
        'principal_label': grant.principal_label,
        'user_id': grant.user_id,
        'group_namespace_id': grant.group_namespace_id,
        'role': grant.role,
        'created_at': grant.created_at,
        'updated_at': grant.updated_at,
    }


@router.post('', response={201: DatasetResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse, 422: ErrorResponse, 503: ErrorResponse})
def create_dataset_endpoint(request, payload: DatasetCreateInput):
    """Create a private dataset and its bare Git repository.

    Parameters
    ----------
    request : django.http.HttpRequest
        Authenticated Django request.
    payload : DatasetCreateInput
        Namespace identity and human-facing dataset details.

    Returns
    -------
    ninja.Status
        Created dataset or a public error response.
    """

    require_access(request=request, scope='api')
    namespace = Namespace.objects.filter(pk=payload.namespace_id).first()
    if namespace is None:
        return Status(404, {'code': 'namespace_not_found', 'detail': 'The requested namespace does not exist.'})

    try:
        dataset = create_dataset(namespace=namespace, slug=payload.slug, name=payload.name, created_by=request.auth)
    except PermissionDenied:
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot create a dataset in this namespace.'})
    except (DatasetPathConflict, IntegrityError):
        return Status(409, {'code': 'dataset_path_conflict', 'detail': 'That namespace path is already in use.'})
    except ValidationError:
        return Status(422, {'code': 'validation_error', 'detail': 'The dataset details are invalid.'})
    except RepositoryProvisioningError:
        # Git and filesystem details are deliberately kept behind the repository boundary.
        return Status(503, {'code': 'repository_unavailable', 'detail': 'The dataset repository could not be created.'})

    return Status(201, serialize_dataset(dataset))


@router.get('', response={200: DatasetListResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 422: ErrorResponse})
def list_datasets_endpoint(request, namespace_id: UUID, limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0)):
    """List one page of datasets in a visible personal namespace.

    Parameters
    ----------
    request : django.http.HttpRequest
        Authenticated Django request.
    namespace_id : uuid.UUID
        Immutable namespace identity.
    limit : int
        Maximum number of datasets to return.
    offset : int
        Number of ordered datasets to skip.

    Returns
    -------
    ninja.Status
        Dataset page or a public not-found response.
    """

    access_token = get_access_token(request)
    boundary_dataset_id = access_token.dataset_id if access_token is not None and access_token.resource_boundary == AccessToken.ResourceBoundary.DATASET else None
    require_access(request=request, scope='read_api', dataset_id=boundary_dataset_id)
    if boundary_dataset_id is not None and access_token.dataset.namespace_id != namespace_id:
        raise AuthorizationError
    datasets = list_visible_namespace_datasets(namespace_id=namespace_id, user=request.auth)
    if datasets is None:
        return Status(404, {'code': 'namespace_not_found', 'detail': 'The requested namespace does not exist.'})
    if boundary_dataset_id is not None:
        datasets = datasets.filter(pk=boundary_dataset_id)

    return Status(200, {'count': datasets.count(), 'limit': limit, 'offset': offset, 'items': [serialize_dataset(dataset) for dataset in datasets[offset : offset + limit]]})


@router.get('/resolve', response={200: DatasetRepositoryLocationResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 422: ErrorResponse})
def resolve_dataset_repository_endpoint(request, path: str = Query(..., min_length=3, max_length=2048)):
    """Resolve a human-facing path for CLI repository operations."""

    dataset = get_visible_dataset_by_path(dataset_path=path, user=request.auth)
    if dataset is None:
        return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
    require_access(request=request, scope='read_repository', dataset_id=dataset.id)
    git_path = reverse('git-dataset-root', kwargs={'dataset_id': dataset.id})
    return {'id': dataset.id, 'path': dataset.path, 'name': dataset.name, 'git_url': request.build_absolute_uri(git_path)}


@router.get('/{dataset_id}', response={200: DatasetResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 422: ErrorResponse})
def get_dataset_endpoint(request, dataset_id: UUID):
    """Retrieve a dataset by immutable identity when visible to the caller.

    Parameters
    ----------
    request : django.http.HttpRequest
        Authenticated Django request.
    dataset_id : uuid.UUID
        Immutable dataset identity.

    Returns
    -------
    ninja.Status
        Dataset representation or a public not-found response.
    """

    require_access(request=request, scope='read_api', dataset_id=dataset_id)
    dataset = get_visible_dataset(dataset_id=dataset_id, user=request.auth)
    if dataset is None:
        return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
    return Status(200, serialize_dataset(dataset))


@router.patch('/{dataset_id}', response={200: DatasetResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse, 422: ErrorResponse})
def update_dataset_endpoint(request, dataset_id: UUID, payload: DatasetUpdateInput):
    """Update a dataset's display name or mutable path slug.

    Parameters
    ----------
    request : django.http.HttpRequest
        Authenticated Django request.
    dataset_id : uuid.UUID
        Immutable dataset identity.
    payload : DatasetUpdateInput
        Requested mutable field values.

    Returns
    -------
    ninja.Status
        Updated dataset or a public error response.
    """

    require_access(request=request, scope='api', dataset_id=dataset_id)
    dataset = get_visible_dataset(dataset_id=dataset_id, user=request.auth)
    if dataset is None:
        return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})

    try:
        dataset = update_dataset(dataset=dataset, updated_by=request.auth, slug=payload.slug, name=payload.name)
    except PermissionDenied:
        # Missing and inaccessible datasets intentionally have the same public response.
        return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
    except (DatasetPathConflict, IntegrityError):
        return Status(409, {'code': 'dataset_path_conflict', 'detail': 'That namespace path is already in use.'})
    except ValidationError:
        return Status(422, {'code': 'validation_error', 'detail': 'The dataset details are invalid.'})

    return Status(200, serialize_dataset(dataset))


@router.delete('/{dataset_id}', response={204: None, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 503: ErrorResponse})
def delete_dataset_endpoint(request, dataset_id: UUID):
    """Permanently delete a dataset and its bare Git repository.

    Parameters
    ----------
    request : django.http.HttpRequest
        Authenticated Django request.
    dataset_id : uuid.UUID
        Immutable dataset identity.

    Returns
    -------
    ninja.Status
        Empty success response or a public error response.
    """

    require_access(request=request, scope='api', dataset_id=dataset_id)
    dataset = get_deletable_dataset(dataset_id=dataset_id, user=request.auth)
    if dataset is None:
        return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})

    try:
        delete_dataset(dataset=dataset, deleted_by=request.auth)
    except PermissionDenied:
        return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
    except RepositoryDeletionError:
        return Status(503, {'code': 'repository_unavailable', 'detail': 'The dataset repository could not be deleted.'})

    return Status(204, None)


@router.get('/{dataset_id}/grants', response={200: DatasetGrantListResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse})
def list_dataset_grants_endpoint(request, dataset_id: UUID):
    """List explicit grants when the caller owns dataset access policy."""

    require_access(request=request, scope='read_api', dataset_id=dataset_id)
    dataset = get_grant_manageable_dataset(dataset_id=dataset_id, user=request.auth)
    if dataset is None:
        return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
    grants = dataset.grants.select_related('user', 'group_namespace__parent').order_by('created_at', 'id')
    return {'count': grants.count(), 'items': [serialize_dataset_grant(grant) for grant in grants]}


@router.post('/{dataset_id}/grants', response={201: DatasetGrantResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse, 422: ErrorResponse})
def create_dataset_grant_endpoint(request, dataset_id: UUID, payload: DatasetGrantCreateInput):
    """Grant a dataset role to one user or Niyān group."""

    require_access(request=request, scope='api', dataset_id=dataset_id)
    dataset = get_grant_manageable_dataset(dataset_id=dataset_id, user=request.auth)
    if dataset is None:
        return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})

    user = get_user_model().objects.filter(pk=payload.user_id).first() if payload.user_id is not None else None
    group_namespace = Namespace.objects.filter(pk=payload.group_namespace_id, kind=Namespace.Kind.GROUP).first() if payload.group_namespace_id is not None else None
    if (payload.user_id is not None and user is None) or (payload.group_namespace_id is not None and group_namespace is None):
        return Status(404, {'code': 'principal_not_found', 'detail': 'The requested grant principal does not exist.'})

    try:
        grant = create_dataset_grant(dataset=dataset, role=payload.role, granted_by=request.auth, user=user, group_namespace=group_namespace)
    except PermissionDenied:
        return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
    except IntegrityError:
        return Status(409, {'code': 'grant_conflict', 'detail': 'That principal already has an explicit grant.'})
    except ValidationError:
        return Status(422, {'code': 'validation_error', 'detail': 'The dataset grant is invalid.'})
    return Status(201, serialize_dataset_grant(grant))


@router.patch('/{dataset_id}/grants/{grant_id}', response={200: DatasetGrantResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 422: ErrorResponse})
def update_dataset_grant_endpoint(request, dataset_id: UUID, grant_id: int, payload: DatasetGrantUpdateInput):
    """Change the role assigned by one explicit dataset grant."""

    require_access(request=request, scope='api', dataset_id=dataset_id)
    dataset = get_grant_manageable_dataset(dataset_id=dataset_id, user=request.auth)
    if dataset is None:
        return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
    grant = DatasetGrant.objects.select_related('user', 'group_namespace__parent').filter(pk=grant_id, dataset=dataset).first()
    if grant is None:
        return Status(404, {'code': 'grant_not_found', 'detail': 'The requested dataset grant does not exist.'})
    try:
        grant = update_dataset_grant(grant=grant, role=payload.role, updated_by=request.auth)
    except PermissionDenied:
        return Status(404, {'code': 'grant_not_found', 'detail': 'The requested dataset grant does not exist.'})
    except ValidationError:
        return Status(422, {'code': 'validation_error', 'detail': 'The dataset grant is invalid.'})
    return serialize_dataset_grant(grant)


@router.delete('/{dataset_id}/grants/{grant_id}', response={204: None, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse})
def delete_dataset_grant_endpoint(request, dataset_id: UUID, grant_id: int):
    """Remove one explicit dataset grant."""

    require_access(request=request, scope='api', dataset_id=dataset_id)
    dataset = get_grant_manageable_dataset(dataset_id=dataset_id, user=request.auth)
    if dataset is None:
        return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
    grant = DatasetGrant.objects.filter(pk=grant_id, dataset=dataset).first()
    if grant is None:
        return Status(404, {'code': 'grant_not_found', 'detail': 'The requested dataset grant does not exist.'})
    try:
        delete_dataset_grant(grant=grant, deleted_by=request.auth)
    except PermissionDenied:
        return Status(404, {'code': 'grant_not_found', 'detail': 'The requested dataset grant does not exist.'})
    return Status(204, None)
