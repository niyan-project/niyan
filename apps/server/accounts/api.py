from datetime import datetime
from urllib.parse import urlencode
from uuid import UUID

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Q
from django.middleware.csrf import CsrfViewMiddleware, get_token
from django.utils import timezone
from ninja import Field, Query, Router, Schema, Status
from ninja.security import django_auth
from ninja.throttling import AnonRateThrottle, UserRateThrottle

from accounts.authentication import get_access_token, session_or_access_token
from accounts.models import AccessToken, DeviceAuthorization
from accounts.tokens import (
    DeviceAuthorizationDenied,
    DeviceAuthorizationExpired,
    DeviceAuthorizationPending,
    DeviceAuthorizationUnavailable,
    DeviceCodeConsumed,
    DevicePollingTooFast,
    InvalidDeviceCode,
    create_access_token,
    decide_device_authorization,
    exchange_device_code,
    revoke_access_token,
    start_device_authorization,
)
from datasets.selectors import get_visible_dataset


class ErrorResponse(Schema):
    """Provide a stable, implementation-neutral authentication error."""

    code: str
    detail: str


class AccessTokenCreateInput(Schema):
    """Describe a manually issued access-token request."""

    name: str = Field(min_length=1, max_length=255)
    scopes: list[str] = Field(min_length=1)
    dataset_id: UUID | None = None
    expires_at: datetime | None = None


class AccessTokenMetadataResponse(Schema):
    """Expose non-secret token lifecycle and resource metadata."""

    id: UUID
    name: str
    origin: str
    fingerprint: str
    resource_boundary: str
    dataset_id: UUID | None
    dataset_path: str | None
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    revoked_at: datetime | None
    active: bool


class AccessTokenCreatedResponse(AccessTokenMetadataResponse):
    """Return a complete access-token secret exactly once."""

    token: str


class AccessTokenListResponse(Schema):
    """Return all token metadata for the signed-in user."""

    count: int
    items: list[AccessTokenMetadataResponse]


class CurrentUserResponse(Schema):
    """Describe the actor and credential used for one request."""

    id: int
    username: str
    display_name: str
    email: str
    is_superuser: bool
    authentication_method: str
    access_token: AccessTokenMetadataResponse | None


class SessionLoginInput(Schema):
    """Carry credentials for one same-origin browser session."""

    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


class EmailChangeInput(Schema):
    """Confirm the current password before replacing an account email."""

    current_password: str = Field(min_length=1, max_length=1024)
    email: str = Field(max_length=254)


class PasswordChangeInput(Schema):
    """Carry current and replacement credentials for a password change."""

    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class CsrfResponse(Schema):
    """Return the token mirrored in Django's CSRF cookie."""

    csrf_token: str


class UserSearchItemResponse(Schema):
    """Expose one active user as an access-management candidate."""

    id: int
    username: str
    display_name: str


class UserSearchResponse(Schema):
    """Return bounded active-user search results."""

    items: list[UserSearchItemResponse]


class DeviceAuthorizationStartInput(Schema):
    """Describe a browser-assisted CLI login request."""

    name: str = Field(min_length=1, max_length=255)
    scopes: list[str] = Field(min_length=1)
    dataset_path: str = Field(default='', max_length=255)


class DeviceAuthorizationStartResponse(Schema):
    """Provide the CLI with private and human-readable device codes."""

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


class DeviceAuthorizationReviewResponse(Schema):
    """Describe a pending request without exposing private device data."""

    user_code: str
    name: str
    scopes: list[str]
    dataset_path: str | None
    expires_at: datetime


class DeviceAuthorizationDecisionInput(Schema):
    """Accept the browser user's device-login decision."""

    approve: bool


class DeviceAuthorizationDecisionResponse(Schema):
    """Confirm that a device request was approved or denied."""

    status: str


class DeviceCodeExchangeInput(Schema):
    """Carry the private code used only by the requesting CLI."""

    device_code: str = Field(min_length=1, max_length=320)


class DeviceCodeExchangeResponse(Schema):
    """Return a CLI access token after browser approval."""

    token: str
    token_type: str
    expires_at: datetime
    scopes: list[str]
    resource_boundary: str
    dataset_id: UUID | None


class DeviceStartRateThrottle(AnonRateThrottle):
    """Limit unauthenticated device requests by client address."""

    scope = 'device_start'


class DevicePollRateThrottle(AnonRateThrottle):
    """Limit device-code exchange traffic by client address."""

    scope = 'device_poll'


class DeviceReviewRateThrottle(UserRateThrottle):
    """Limit device-code review lookups per browser user."""

    scope = 'device_review'


class DeviceDecisionRateThrottle(UserRateThrottle):
    """Limit device-code decisions per browser user."""

    scope = 'device_decision'


router = Router(tags=['authentication'])
device_start_throttle = DeviceStartRateThrottle('10/min')
device_poll_throttle = DevicePollRateThrottle('60/min')
device_review_throttle = DeviceReviewRateThrottle('60/min')
device_decision_throttle = DeviceDecisionRateThrottle('20/min')


def serialize_access_token(access_token):
    """Convert token metadata into its non-secret public representation.

    Parameters
    ----------
    access_token : accounts.models.AccessToken
        Token whose dataset and namespace may be loaded.

    Returns
    -------
    dict
        Fields safe to return to the token owner.
    """

    dataset = access_token.dataset
    return {
        'id': access_token.id,
        'name': access_token.name,
        'origin': access_token.origin,
        'fingerprint': access_token.fingerprint,
        'resource_boundary': access_token.resource_boundary,
        'dataset_id': access_token.dataset_id,
        'dataset_path': dataset.path if dataset is not None else None,
        'scopes': access_token.scopes,
        'created_at': access_token.created_at,
        'last_used_at': access_token.last_used_at,
        'expires_at': access_token.expires_at,
        'revoked_at': access_token.revoked_at,
        'active': access_token.is_active(),
    }


def serialize_current_user(user, *, authentication_method='session', access_token=None):
    """Convert one authenticated user into browser-safe account state."""

    return {
        'id': user.id,
        'username': user.username,
        'display_name': user.get_full_name().strip() or user.username,
        'email': user.email,
        'is_superuser': user.is_superuser,
        'authentication_method': authentication_method,
        'access_token': serialize_access_token(access_token) if access_token is not None else None,
    }


def passes_csrf_check(request):
    """Apply Django's CSRF validator inside an anonymous Ninja operation."""

    middleware = CsrfViewMiddleware(lambda checked_request: None)
    return middleware.process_view(request, lambda checked_request: None, (), {}) is None


@router.get('/csrf', auth=None, response={200: CsrfResponse})
def get_csrf_token_endpoint(request):
    """Initialize the same-origin CSRF cookie before browser mutation."""

    return {'csrf_token': get_token(request)}


@router.post('/session', auth=None, response={200: CurrentUserResponse, 401: ErrorResponse, 403: ErrorResponse})
def create_session_endpoint(request, payload: SessionLoginInput):
    """Authenticate an existing active user into a Django browser session."""

    if not passes_csrf_check(request):
        return Status(403, {'code': 'csrf_failed', 'detail': 'The CSRF token is missing or invalid.'})
    user = authenticate(request, username=payload.username, password=payload.password)
    if user is None or not user.is_active:
        return Status(401, {'code': 'invalid_credentials', 'detail': 'The username or password is incorrect.'})
    login(request, user)
    return serialize_current_user(user)


@router.delete('/session', auth=django_auth, response={204: None, 401: ErrorResponse})
def delete_session_endpoint(request):
    """End the current Django browser session."""

    logout(request)
    return Status(204, None)


@router.get('/users', auth=django_auth, response={200: UserSearchResponse, 401: ErrorResponse, 422: ErrorResponse})
def search_users_endpoint(request, query: str = Query(..., min_length=2, max_length=100), limit: int = Query(20, ge=1, le=20)):
    """Search active users for group memberships and dataset grants."""

    normalized_query = query.strip()
    users = get_user_model().objects.filter(is_active=True).filter(Q(username__icontains=normalized_query) | Q(first_name__icontains=normalized_query) | Q(last_name__icontains=normalized_query)).order_by('username', 'id')[:limit]
    return {'items': [{'id': user.id, 'username': user.username, 'display_name': user.get_full_name().strip() or user.username} for user in users]}


@router.get('/tokens', auth=django_auth, response={200: AccessTokenListResponse, 401: ErrorResponse})
def list_access_tokens_endpoint(request):
    """List every access token issued to the browser user."""

    tokens = AccessToken.objects.select_related('dataset__namespace').filter(user=request.auth)
    return {'count': tokens.count(), 'items': [serialize_access_token(access_token) for access_token in tokens]}


@router.post('/tokens', auth=django_auth, response={201: AccessTokenCreatedResponse, 401: ErrorResponse, 404: ErrorResponse, 422: ErrorResponse})
def create_access_token_endpoint(request, payload: AccessTokenCreateInput):
    """Create a manually issued access token and reveal it once."""

    dataset = None
    if payload.dataset_id is not None:
        dataset = get_visible_dataset(dataset_id=payload.dataset_id, user=request.auth)
        if dataset is None:
            return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})

    try:
        access_token, raw_token = create_access_token(
            user=request.auth,
            name=payload.name,
            scopes=payload.scopes,
            origin=AccessToken.Origin.MANUAL,
            dataset=dataset,
            expires_at=payload.expires_at,
        )
    except ValidationError:
        return Status(422, {'code': 'validation_error', 'detail': 'The access-token details are invalid.'})

    response = serialize_access_token(access_token)
    response['token'] = raw_token
    return Status(201, response)


@router.delete('/tokens/{token_id}', auth=session_or_access_token, response={204: None, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse})
def revoke_access_token_endpoint(request, token_id: UUID):
    """Revoke a browser-selected token or the active bearer token itself."""

    authenticating_token = get_access_token(request)
    if authenticating_token is not None and authenticating_token.id != token_id:
        return Status(403, {'code': 'token_self_revocation_required', 'detail': 'An access token may revoke only itself.'})

    access_token = AccessToken.objects.filter(pk=token_id, user=request.auth).first()
    if access_token is None:
        return Status(404, {'code': 'token_not_found', 'detail': 'The requested access token does not exist.'})
    revoke_access_token(access_token=access_token, revoked_by=request.auth, authenticating_token=authenticating_token)
    return Status(204, None)


@router.get('/me', auth=session_or_access_token, response={200: CurrentUserResponse, 401: ErrorResponse})
def get_current_user_endpoint(request):
    """Inspect the authenticated user and optional bearer-token metadata."""

    access_token = get_access_token(request)
    return serialize_current_user(request.auth, authentication_method='access_token' if access_token is not None else 'session', access_token=access_token)


@router.patch('/me/email', auth=django_auth, response={200: CurrentUserResponse, 400: ErrorResponse, 401: ErrorResponse, 422: ErrorResponse})
def change_email_endpoint(request, payload: EmailChangeInput):
    """Replace the browser user's email after checking their password."""

    if not request.auth.check_password(payload.current_password):
        return Status(400, {'code': 'invalid_current_password', 'detail': 'The current password is incorrect.'})

    email = get_user_model().objects.normalize_email(payload.email.strip())
    try:
        validate_email(email)
    except ValidationError:
        return Status(422, {'code': 'validation_error', 'detail': 'Enter a valid email address.'})

    request.auth.email = email
    request.auth.save(update_fields=['email'])
    return serialize_current_user(request.auth)


@router.patch('/me/password', auth=django_auth, response={204: None, 400: ErrorResponse, 401: ErrorResponse, 422: ErrorResponse})
def change_password_endpoint(request, payload: PasswordChangeInput):
    """Replace the browser user's password while retaining this session."""

    if not request.auth.check_password(payload.current_password):
        return Status(400, {'code': 'invalid_current_password', 'detail': 'The current password is incorrect.'})

    try:
        validate_password(payload.new_password, user=request.auth)
    except ValidationError:
        return Status(422, {'code': 'validation_error', 'detail': 'The new password does not meet this installation\'s password requirements.'})

    request.auth.set_password(payload.new_password)
    request.auth.save(update_fields=['password'])
    update_session_auth_hash(request, request.auth)
    return Status(204, None)


@router.post('/device', auth=None, throttle=[device_start_throttle], response={201: DeviceAuthorizationStartResponse, 422: ErrorResponse, 429: ErrorResponse})
def start_device_authorization_endpoint(request, payload: DeviceAuthorizationStartInput):
    """Create the short-lived codes used by browser-assisted CLI login."""

    try:
        authorization, device_code = start_device_authorization(name=payload.name, scopes=payload.scopes, requested_dataset_path=payload.dataset_path)
    except ValidationError:
        return Status(422, {'code': 'validation_error', 'detail': 'The device authorization request is invalid.'})

    verification_uri = request.build_absolute_uri('/auth/device')
    return Status(
        201,
        {
            'device_code': device_code,
            'user_code': authorization.user_code,
            'verification_uri': verification_uri,
            'verification_uri_complete': f'{verification_uri}?{urlencode({"user_code": authorization.user_code})}',
            'expires_in': max(0, int((authorization.expires_at - timezone.now()).total_seconds())),
            'interval': settings.NIYAN_DEVICE_AUTHORIZATION_POLL_INTERVAL_SECONDS,
        },
    )


@router.post('/device/token', auth=None, throttle=[device_poll_throttle], response={200: DeviceCodeExchangeResponse, 202: ErrorResponse, 400: ErrorResponse, 403: ErrorResponse, 422: ErrorResponse, 429: ErrorResponse})
def exchange_device_code_endpoint(request, payload: DeviceCodeExchangeInput):
    """Exchange one approved private device code for a CLI token."""

    try:
        access_token, raw_token = exchange_device_code(raw_device_code=payload.device_code)
    except DeviceAuthorizationPending:
        return Status(202, {'code': 'authorization_pending', 'detail': 'The device authorization is awaiting a browser decision.'})
    except DeviceAuthorizationDenied:
        return Status(403, {'code': 'access_denied', 'detail': 'The device authorization was denied.'})
    except DeviceAuthorizationExpired:
        return Status(400, {'code': 'expired_device_code', 'detail': 'The device authorization has expired.'})
    except DeviceCodeConsumed:
        return Status(400, {'code': 'device_code_used', 'detail': 'The device authorization has already been exchanged.'})
    except InvalidDeviceCode:
        return Status(400, {'code': 'invalid_device_code', 'detail': 'The device code is invalid.'})
    except DevicePollingTooFast:
        return Status(429, {'code': 'slow_down', 'detail': 'Wait before polling this device authorization again.'})
    except ValidationError:
        return Status(422, {'code': 'authorization_unavailable', 'detail': 'The approved resource is no longer available.'})

    return {
        'token': raw_token,
        'token_type': 'Bearer',
        'expires_at': access_token.expires_at,
        'scopes': access_token.scopes,
        'resource_boundary': access_token.resource_boundary,
        'dataset_id': access_token.dataset_id,
    }


@router.get('/device/{user_code}', auth=django_auth, throttle=[device_review_throttle], response={200: DeviceAuthorizationReviewResponse, 401: ErrorResponse, 404: ErrorResponse, 429: ErrorResponse})
def inspect_device_authorization_endpoint(request, user_code: str):
    """Show a browser user the scopes and boundary awaiting review."""

    authorization = DeviceAuthorization.objects.filter(user_code=user_code.strip().upper(), status=DeviceAuthorization.Status.PENDING, expires_at__gt=timezone.now()).first()
    if authorization is None:
        return Status(404, {'code': 'device_authorization_not_found', 'detail': 'The device authorization does not exist.'})
    return {
        'user_code': authorization.user_code,
        'name': authorization.name,
        'scopes': authorization.requested_scopes,
        'dataset_path': authorization.requested_dataset_path or None,
        'expires_at': authorization.expires_at,
    }


@router.post('/device/{user_code}', auth=django_auth, throttle=[device_decision_throttle], response={200: DeviceAuthorizationDecisionResponse, 401: ErrorResponse, 404: ErrorResponse, 422: ErrorResponse, 429: ErrorResponse})
def decide_device_authorization_endpoint(request, user_code: str, payload: DeviceAuthorizationDecisionInput):
    """Approve or deny a device authorization through a browser session."""

    try:
        authorization = decide_device_authorization(user_code=user_code, user=request.auth, approve=payload.approve)
    except DeviceAuthorizationUnavailable:
        return Status(404, {'code': 'device_authorization_not_found', 'detail': 'The device authorization does not exist.'})
    except ValidationError:
        return Status(422, {'code': 'dataset_unavailable', 'detail': 'The requested dataset is not available to this user.'})
    return {'status': authorization.status}
