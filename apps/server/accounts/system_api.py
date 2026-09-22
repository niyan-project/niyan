from datetime import datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from ninja import Field, Query, Router, Schema, Status
from ninja.security import django_auth

from accounts.permissions import SYSTEM_PERMISSIONS, has_system_permission
from accounts.services import create_user


class ErrorResponse(Schema):
    """Provide a stable system-administration API error."""

    code: str
    detail: str


class SystemUserCreateInput(Schema):
    """Describe an account provisioned by an authorized staff user."""

    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)
    email: str = Field(default='', max_length=254)
    first_name: str = Field(default='', max_length=150)
    last_name: str = Field(default='', max_length=150)


class SystemUserUpdateInput(Schema):
    """Describe editable account state exposed to delegated administrators."""

    email: str | None = Field(default=None, max_length=254)
    first_name: str | None = Field(default=None, max_length=150)
    last_name: str | None = Field(default=None, max_length=150)
    is_active: bool | None = None
    is_staff: bool | None = None
    permission_group_ids: list[int] | None = None


class SystemUserResponse(Schema):
    """Expose non-secret account metadata to installation administrators."""

    id: int
    username: str
    display_name: str
    email: str
    first_name: str
    last_name: str
    is_active: bool
    is_staff: bool
    is_superuser: bool
    date_joined: datetime
    last_login: datetime | None
    permission_group_ids: list[int]


class SystemUserListResponse(Schema):
    """Describe one limit-and-offset page of installation users."""

    count: int
    limit: int
    offset: int
    items: list[SystemUserResponse]


class PermissionGroupInput(Schema):
    """Describe one Django permission group managed through Niyān."""

    name: str = Field(min_length=1, max_length=150)
    permissions: list[str] = Field(default_factory=list)


class PermissionGroupResponse(Schema):
    """Expose a Django group through stable product permission names."""

    id: int
    name: str
    permissions: list[str]


class PermissionGroupListResponse(Schema):
    """List Django permission groups and assignable system permissions."""

    count: int
    available_permissions: list[str]
    items: list[PermissionGroupResponse]


router = Router(tags=['system'], auth=django_auth)


def serialize_system_user(user):
    """Convert one user into system-administration metadata."""

    return {
        'id': user.id,
        'username': user.username,
        'display_name': user.get_full_name().strip() or user.username,
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'is_active': user.is_active,
        'is_staff': user.is_staff,
        'is_superuser': user.is_superuser,
        'date_joined': user.date_joined,
        'last_login': user.last_login,
        'permission_group_ids': sorted(group.id for group in user.groups.all()),
    }


def serialize_permission_group(group):
    """Convert one Django group without exposing unrelated model permissions."""

    public_by_django = {}
    for public_name, django_name in SYSTEM_PERMISSIONS.items():
        public_by_django.setdefault(django_name, []).append(public_name)
    assigned = []
    for permission in group.permissions.select_related('content_type').all():
        django_name = f'{permission.content_type.app_label}.{permission.codename}'
        assigned.extend(public_by_django.get(django_name, []))
    return {'id': group.id, 'name': group.name, 'permissions': sorted(set(assigned))}


def resolve_permissions(public_names):
    """Resolve only documented system permissions to Django rows."""

    requested = set(public_names)
    if len(requested) != len(public_names) or not requested.issubset(SYSTEM_PERMISSIONS):
        raise ValidationError('Unknown or duplicate system permission.')
    django_names = {SYSTEM_PERMISSIONS[name] for name in requested}
    permissions = Permission.objects.select_related('content_type').filter(
        content_type__app_label__in={name.split('.', 1)[0] for name in django_names},
        codename__in={name.split('.', 1)[1] for name in django_names},
    )
    resolved = {f'{permission.content_type.app_label}.{permission.codename}': permission for permission in permissions}
    if django_names - resolved.keys():
        raise ValidationError('A configured system permission is unavailable.')
    return [resolved[name] for name in django_names]


@router.get('/users', response={200: SystemUserListResponse, 401: ErrorResponse, 403: ErrorResponse, 422: ErrorResponse})
def list_users_endpoint(request, limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0)):
    """List installation users when the staff actor may view accounts."""

    if not has_system_permission(user=request.auth, permission='users.view'):
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot view installation users.'})
    users = get_user_model().objects.prefetch_related('groups').order_by('username', 'id')
    return {'count': users.count(), 'limit': limit, 'offset': offset, 'items': [serialize_system_user(user) for user in users[offset : offset + limit]]}


@router.post('/users', response={201: SystemUserResponse, 401: ErrorResponse, 403: ErrorResponse, 409: ErrorResponse, 422: ErrorResponse})
def create_user_endpoint(request, payload: SystemUserCreateInput):
    """Provision one active account through the staff-only system surface."""

    if not has_system_permission(user=request.auth, permission='users.add'):
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot create installation users.'})
    try:
        user = create_user(
            username=payload.username,
            password=payload.password,
            email=payload.email,
            first_name=payload.first_name,
            last_name=payload.last_name,
            created_by=request.auth,
        )
    except IntegrityError:
        return Status(409, {'code': 'username_conflict', 'detail': 'That username or personal namespace path is already in use.'})
    except ValidationError as error:
        username_messages = getattr(error, 'message_dict', {}).get('username', [])
        if any('already exists' in message or 'conflicts' in message for message in username_messages):
            return Status(409, {'code': 'username_conflict', 'detail': 'That username or personal namespace path is already in use.'})
        return Status(422, {'code': 'validation_error', 'detail': 'The user details or password are invalid.'})
    return Status(201, serialize_system_user(user))


@router.patch('/users/{user_id}', response={200: SystemUserResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 422: ErrorResponse})
def update_user_endpoint(request, user_id: int, payload: SystemUserUpdateInput):
    """Update non-secret profile, activation, and delegated-staff state."""

    if not has_system_permission(user=request.auth, permission='users.change'):
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot change installation users.'})
    with transaction.atomic():
        target = get_user_model().objects.select_for_update().filter(pk=user_id).first()
        if target is None:
            return Status(404, {'code': 'not_found', 'detail': 'Installation user not found.'})
        if target.is_superuser and not request.auth.is_superuser:
            return Status(403, {'code': 'permission_denied', 'detail': 'Only a superuser can change another superuser.'})
        values = payload.dict(exclude_unset=True)
        permission_group_ids = values.pop('permission_group_ids', None)
        if permission_group_ids is not None and not has_system_permission(user=request.auth, permission='permission_groups.change'):
            return Status(403, {'code': 'permission_denied', 'detail': 'You cannot assign permission groups.'})
        permission_groups = None
        if permission_group_ids is not None:
            if len(set(permission_group_ids)) != len(permission_group_ids):
                return Status(422, {'code': 'validation_error', 'detail': 'The permission-group selection is invalid.'})
            permission_groups = list(Group.objects.filter(pk__in=permission_group_ids))
            if len(permission_groups) != len(permission_group_ids):
                return Status(422, {'code': 'validation_error', 'detail': 'The permission-group selection is invalid.'})
        if target.pk == request.auth.pk and values.get('is_active') is False:
            return Status(422, {'code': 'validation_error', 'detail': 'You cannot deactivate your own account.'})
        if target.pk == request.auth.pk and values.get('is_staff') is False:
            return Status(422, {'code': 'validation_error', 'detail': 'You cannot remove your own staff access.'})
        for field, value in values.items():
            setattr(target, field, value)
        try:
            target.full_clean(exclude=['password'])
            if values:
                target.save(update_fields=[*values.keys()])
            if permission_groups is not None:
                target.groups.set(permission_groups)
        except ValidationError:
            return Status(422, {'code': 'validation_error', 'detail': 'The user details are invalid.'})
    return serialize_system_user(target)


@router.get('/staff', response={200: SystemUserListResponse, 401: ErrorResponse, 403: ErrorResponse, 422: ErrorResponse})
def list_staff_endpoint(request, limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0)):
    """List staff accounts for delegated administration."""

    if not has_system_permission(user=request.auth, permission='staff.view'):
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot view staff accounts.'})
    users = get_user_model().objects.filter(is_staff=True).prefetch_related('groups').order_by('username', 'id')
    return {'count': users.count(), 'limit': limit, 'offset': offset, 'items': [serialize_system_user(user) for user in users[offset : offset + limit]]}


@router.get('/permission-groups', response={200: PermissionGroupListResponse, 401: ErrorResponse, 403: ErrorResponse})
def list_permission_groups_endpoint(request):
    """List Django groups used to delegate the System surface."""

    if not has_system_permission(user=request.auth, permission='permission_groups.view'):
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot view permission groups.'})
    groups = Group.objects.prefetch_related('permissions__content_type').order_by('name', 'id')
    return {'count': groups.count(), 'available_permissions': sorted(SYSTEM_PERMISSIONS), 'items': [serialize_permission_group(group) for group in groups]}


@router.post('/permission-groups', response={201: PermissionGroupResponse, 401: ErrorResponse, 403: ErrorResponse, 409: ErrorResponse, 422: ErrorResponse})
def create_permission_group_endpoint(request, payload: PermissionGroupInput):
    """Create one Django group with an explicit System permission set."""

    if not has_system_permission(user=request.auth, permission='permission_groups.add'):
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot create permission groups.'})
    try:
        permissions = resolve_permissions(payload.permissions)
        with transaction.atomic():
            group = Group.objects.create(name=payload.name)
            group.permissions.set(permissions)
    except IntegrityError:
        return Status(409, {'code': 'name_conflict', 'detail': 'A permission group with that name already exists.'})
    except ValidationError:
        return Status(422, {'code': 'validation_error', 'detail': 'The permission group is invalid.'})
    return Status(201, serialize_permission_group(group))


@router.put('/permission-groups/{group_id}', response={200: PermissionGroupResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse, 422: ErrorResponse})
def update_permission_group_endpoint(request, group_id: int, payload: PermissionGroupInput):
    """Replace one Django group's name and System permission set."""

    if not has_system_permission(user=request.auth, permission='permission_groups.change'):
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot change permission groups.'})
    try:
        permissions = resolve_permissions(payload.permissions)
        with transaction.atomic():
            group = Group.objects.select_for_update().filter(pk=group_id).first()
            if group is None:
                return Status(404, {'code': 'not_found', 'detail': 'Permission group not found.'})
            group.name = payload.name
            group.full_clean()
            group.save(update_fields=['name'])
            group.permissions.set(permissions)
    except IntegrityError:
        return Status(409, {'code': 'name_conflict', 'detail': 'A permission group with that name already exists.'})
    except ValidationError:
        return Status(422, {'code': 'validation_error', 'detail': 'The permission group is invalid.'})
    return serialize_permission_group(group)


@router.delete('/permission-groups/{group_id}', response={204: None, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse})
def delete_permission_group_endpoint(request, group_id: int):
    """Delete one Django group without deleting its former members."""

    if not has_system_permission(user=request.auth, permission='permission_groups.delete'):
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot delete permission groups.'})
    deleted, _ = Group.objects.filter(pk=group_id).delete()
    if not deleted:
        return Status(404, {'code': 'not_found', 'detail': 'Permission group not found.'})
    return 204, None
