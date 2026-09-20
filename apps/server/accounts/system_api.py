from datetime import datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from ninja import Field, Query, Router, Schema, Status
from ninja.security import django_auth

from accounts.permissions import has_system_permission
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


class SystemUserResponse(Schema):
    """Expose non-secret account metadata to installation administrators."""

    id: int
    username: str
    display_name: str
    email: str
    is_active: bool
    is_staff: bool
    is_superuser: bool
    date_joined: datetime
    last_login: datetime | None


class SystemUserListResponse(Schema):
    """Describe one limit-and-offset page of installation users."""

    count: int
    limit: int
    offset: int
    items: list[SystemUserResponse]


router = Router(tags=['system'], auth=django_auth)


def serialize_system_user(user):
    """Convert one user into system-administration metadata."""

    return {
        'id': user.id,
        'username': user.username,
        'display_name': user.get_full_name().strip() or user.username,
        'email': user.email,
        'is_active': user.is_active,
        'is_staff': user.is_staff,
        'is_superuser': user.is_superuser,
        'date_joined': user.date_joined,
        'last_login': user.last_login,
    }


@router.get('/users', response={200: SystemUserListResponse, 401: ErrorResponse, 403: ErrorResponse, 422: ErrorResponse})
def list_users_endpoint(request, limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0)):
    """List installation users when the staff actor may view accounts."""

    if not has_system_permission(user=request.auth, permission='users.view'):
        return Status(403, {'code': 'permission_denied', 'detail': 'You cannot view installation users.'})
    users = get_user_model().objects.order_by('username', 'id')
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
