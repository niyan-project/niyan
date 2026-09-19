from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from ninja import Field, Query, Router, Schema, Status
from pydantic import model_validator

from accounts.authentication import require_access, session_or_access_token
from datasets.policies import can_create_dataset, can_manage_group, get_namespace_role
from namespaces.models import Namespace
from namespaces.selectors import get_manageable_group, get_visible_namespace, get_visible_namespace_by_path, list_visible_namespaces
from namespaces.services import GroupNotEmpty, GroupPathConflict, LastGroupOwner, create_group, create_group_membership, delete_group, delete_group_membership, update_group, update_group_membership


class NamespaceResolveResponse(Schema):
    """Expose namespace identity needed by path-based API clients."""

    id: UUID
    path: str
    name: str
    kind: str


class NamespaceResponse(Schema):
    """Describe a visible personal namespace or group for browser navigation."""

    id: UUID
    parent_id: UUID | None
    parent_path: str | None
    path: str
    slug: str
    name: str
    kind: str
    role: str | None
    can_create_dataset: bool
    can_manage: bool
    created_at: datetime
    updated_at: datetime


class NamespaceListResponse(Schema):
    """Return one deterministic page of visible namespaces."""

    count: int
    limit: int
    offset: int
    items: list[NamespaceResponse]


class GroupCreateInput(Schema):
    """Describe a root or nested group creation request."""

    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=100)
    parent_id: UUID | None = None


class GroupUpdateInput(Schema):
    """Describe mutable group fields accepted by a partial update."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode='after')
    def require_non_null_change(self) -> Self:
        """Reject empty updates and explicit null values."""

        if not self.model_fields_set or any(getattr(self, field_name) is None for field_name in self.model_fields_set):
            raise ValueError('At least one non-null field is required.')
        return self


class MembershipCreateInput(Schema):
    """Describe one direct user membership in a group."""

    username: str = Field(min_length=1, max_length=150)
    role: Literal['reader', 'contributor', 'maintainer', 'owner']


class MembershipUpdateInput(Schema):
    """Describe a direct group-membership role change."""

    role: Literal['reader', 'contributor', 'maintainer', 'owner']


class MembershipResponse(Schema):
    """Expose one direct group membership."""

    id: int
    namespace_id: UUID
    user_id: int
    username: str
    display_name: str
    role: str
    created_at: datetime
    updated_at: datetime


class MembershipListResponse(Schema):
    """Return direct members of one manageable group."""

    count: int
    items: list[MembershipResponse]


class ErrorResponse(Schema):
    """Provide a stable namespace API error."""

    code: str
    detail: str


router = Router(tags=['namespaces'], auth=session_or_access_token)


def serialize_namespace(namespace, *, user):
    """Convert a visible namespace into browser navigation metadata."""

    role = get_namespace_role(user=user, namespace=namespace)
    return {
        'id': namespace.id,
        'parent_id': namespace.parent_id,
        'parent_path': namespace.parent.path if namespace.parent is not None else None,
        'path': namespace.path,
        'slug': namespace.slug,
        'name': namespace.name,
        'kind': namespace.kind,
        'role': role,
        'can_create_dataset': can_create_dataset(user=user, namespace=namespace),
        'can_manage': can_manage_group(user=user, namespace=namespace),
        'created_at': namespace.created_at,
        'updated_at': namespace.updated_at,
    }


def serialize_membership(membership):
    """Convert direct membership metadata for its group owner."""

    return {
        'id': membership.id,
        'namespace_id': membership.namespace_id,
        'user_id': membership.user_id,
        'username': membership.user.username,
        'display_name': membership.user.get_full_name().strip() or membership.user.username,
        'role': membership.role,
        'created_at': membership.created_at,
        'updated_at': membership.updated_at,
    }


@router.get('', response={200: NamespaceListResponse, 401: ErrorResponse, 403: ErrorResponse, 422: ErrorResponse})
def list_namespaces_endpoint(request, limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0)):
    """List personal and group namespaces visible to the current user."""

    require_access(request=request, scope='read_api')
    namespaces = list_visible_namespaces(user=request.auth)
    return {'count': len(namespaces), 'limit': limit, 'offset': offset, 'items': [serialize_namespace(namespace, user=request.auth) for namespace in namespaces[offset : offset + limit]]}


@router.post('', response={201: NamespaceResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse, 422: ErrorResponse})
def create_group_endpoint(request, payload: GroupCreateInput):
    """Create a root or nested Niyān group and grant its creator ownership."""

    require_access(request=request, scope='api')
    parent = None
    if payload.parent_id is not None:
        parent = get_visible_namespace(namespace_id=payload.parent_id, user=request.auth)
        if parent is None or parent.kind != Namespace.Kind.GROUP:
            return Status(404, {'code': 'group_not_found', 'detail': 'The requested parent group does not exist.'})
    try:
        group = create_group(name=payload.name, slug=payload.slug, created_by=request.auth, parent=parent)
    except PermissionDenied:
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot create a group in this location.'})
    except (GroupPathConflict, IntegrityError):
        return Status(409, {'code': 'group_path_conflict', 'detail': 'That group path is already in use.'})
    except ValidationError:
        return Status(422, {'code': 'validation_error', 'detail': 'The group details are invalid.'})
    return Status(201, serialize_namespace(group, user=request.auth))


@router.get('/resolve', response={200: NamespaceResolveResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 422: ErrorResponse})
def resolve_namespace_endpoint(request, path: str = Query(..., min_length=1, max_length=2048)):
    """Resolve one visible human-facing namespace path."""

    require_access(request=request, scope='read_api')
    namespace = get_visible_namespace_by_path(namespace_path=path, user=request.auth)
    if namespace is None:
        return Status(404, {'code': 'namespace_not_found', 'detail': 'The requested namespace does not exist.'})
    return {'id': namespace.id, 'path': namespace.path, 'name': namespace.name, 'kind': namespace.kind}


@router.get('/{namespace_id}', response={200: NamespaceResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse})
def get_namespace_endpoint(request, namespace_id: UUID):
    """Return one visible namespace by immutable identity."""

    require_access(request=request, scope='read_api')
    namespace = get_visible_namespace(namespace_id=namespace_id, user=request.auth)
    if namespace is None:
        return Status(404, {'code': 'namespace_not_found', 'detail': 'The requested namespace does not exist.'})
    return serialize_namespace(namespace, user=request.auth)


@router.patch('/{namespace_id}', response={200: NamespaceResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse, 422: ErrorResponse})
def update_group_endpoint(request, namespace_id: UUID, payload: GroupUpdateInput):
    """Update the name or slug of one owner-managed group."""

    require_access(request=request, scope='api')
    group = get_manageable_group(namespace_id=namespace_id, user=request.auth)
    if group is None:
        return Status(404, {'code': 'group_not_found', 'detail': 'The requested group does not exist.'})
    try:
        updated = update_group(group=group, updated_by=request.auth, name=payload.name if 'name' in payload.model_fields_set else None, slug=payload.slug if 'slug' in payload.model_fields_set else None)
    except PermissionDenied:
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot update this group.'})
    except (GroupPathConflict, IntegrityError):
        return Status(409, {'code': 'group_path_conflict', 'detail': 'That group path is already in use.'})
    except ValidationError:
        return Status(422, {'code': 'validation_error', 'detail': 'The group details are invalid.'})
    return serialize_namespace(updated, user=request.auth)


@router.delete('/{namespace_id}', response={204: None, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse})
def delete_group_endpoint(request, namespace_id: UUID):
    """Permanently delete one empty owner-managed group."""

    require_access(request=request, scope='api')
    group = get_manageable_group(namespace_id=namespace_id, user=request.auth)
    if group is None:
        return Status(404, {'code': 'group_not_found', 'detail': 'The requested group does not exist.'})
    try:
        delete_group(group=group, deleted_by=request.auth)
    except PermissionDenied:
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot delete this group.'})
    except GroupNotEmpty:
        return Status(409, {'code': 'group_not_empty', 'detail': 'Remove this group\'s datasets and child groups before deleting it.'})
    return Status(204, None)


@router.get('/{namespace_id}/memberships', response={200: MembershipListResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse})
def list_group_memberships_endpoint(request, namespace_id: UUID):
    """List direct memberships for one owner-managed group."""

    require_access(request=request, scope='read_api')
    group = get_manageable_group(namespace_id=namespace_id, user=request.auth)
    if group is None:
        return Status(404, {'code': 'group_not_found', 'detail': 'The requested group does not exist.'})
    memberships = group.memberships.select_related('user').order_by('user__username', 'id')
    return {'count': memberships.count(), 'items': [serialize_membership(membership) for membership in memberships]}


@router.post('/{namespace_id}/memberships', response={201: MembershipResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse, 422: ErrorResponse})
def create_group_membership_endpoint(request, namespace_id: UUID, payload: MembershipCreateInput):
    """Add one direct user membership to an owner-managed group."""

    require_access(request=request, scope='api')
    group = get_manageable_group(namespace_id=namespace_id, user=request.auth)
    if group is None:
        return Status(404, {'code': 'group_not_found', 'detail': 'The requested group does not exist.'})
    user = get_user_model().objects.filter(username=payload.username, is_active=True).first()
    if user is None:
        return Status(404, {'code': 'user_not_found', 'detail': 'The requested user does not exist.'})
    try:
        membership = create_group_membership(group=group, user=user, role=payload.role, created_by=request.auth)
    except PermissionDenied:
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot manage this group.'})
    except IntegrityError:
        return Status(409, {'code': 'membership_exists', 'detail': 'That user already has a direct membership in this group.'})
    except ValidationError:
        return Status(422, {'code': 'validation_error', 'detail': 'The membership details are invalid.'})
    return Status(201, serialize_membership(membership))


@router.patch('/{namespace_id}/memberships/{membership_id}', response={200: MembershipResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse, 422: ErrorResponse})
def update_group_membership_endpoint(request, namespace_id: UUID, membership_id: int, payload: MembershipUpdateInput):
    """Update one direct membership in an owner-managed group."""

    require_access(request=request, scope='api')
    group = get_manageable_group(namespace_id=namespace_id, user=request.auth)
    if group is None:
        return Status(404, {'code': 'group_not_found', 'detail': 'The requested group does not exist.'})
    membership = group.memberships.select_related('user').filter(pk=membership_id).first()
    if membership is None:
        return Status(404, {'code': 'membership_not_found', 'detail': 'The requested membership does not exist.'})
    try:
        updated = update_group_membership(membership=membership, role=payload.role, updated_by=request.auth)
    except PermissionDenied:
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot manage this group.'})
    except LastGroupOwner:
        return Status(409, {'code': 'last_group_owner', 'detail': 'A group must retain at least one direct owner.'})
    except ValidationError:
        return Status(422, {'code': 'validation_error', 'detail': 'The membership details are invalid.'})
    return serialize_membership(updated)


@router.delete('/{namespace_id}/memberships/{membership_id}', response={204: None, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse})
def delete_group_membership_endpoint(request, namespace_id: UUID, membership_id: int):
    """Remove one direct membership from an owner-managed group."""

    require_access(request=request, scope='api')
    group = get_manageable_group(namespace_id=namespace_id, user=request.auth)
    if group is None:
        return Status(404, {'code': 'group_not_found', 'detail': 'The requested group does not exist.'})
    membership = group.memberships.filter(pk=membership_id).first()
    if membership is None:
        return Status(404, {'code': 'membership_not_found', 'detail': 'The requested membership does not exist.'})
    try:
        delete_group_membership(membership=membership, deleted_by=request.auth)
    except PermissionDenied:
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot manage this group.'})
    except LastGroupOwner:
        return Status(409, {'code': 'last_group_owner', 'detail': 'A group must retain at least one direct owner.'})
    return Status(204, None)
