import hashlib
import hmac
import secrets
import string
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import AccessToken, DeviceAuthorization
from datasets.audit import record_audit_event
from datasets.models import AuditEvent
from datasets.selectors import get_visible_dataset, get_visible_dataset_by_path


TOKEN_PREFIX = 'niyan_'
DEVICE_CODE_PREFIX = 'niyan_device_'
ALLOWED_SCOPES = frozenset({'read_api', 'api', 'read_repository', 'write_repository'})
SCOPE_IMPLICATIONS = {
    'read_api': frozenset({'read_api'}),
    'api': frozenset({'api', 'read_api'}),
    'read_repository': frozenset({'read_repository'}),
    'write_repository': frozenset({'write_repository', 'read_repository'}),
}
USER_CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


class InvalidAccessToken(Exception):
    """Report an access token that cannot authenticate."""


class DeviceAuthorizationError(Exception):
    """Base error for a device authorization that cannot be exchanged."""


class InvalidDeviceCode(DeviceAuthorizationError):
    """Report a malformed or unknown private device code."""


class DeviceAuthorizationPending(DeviceAuthorizationError):
    """Report that the user has not decided a device request yet."""


class DeviceAuthorizationDenied(DeviceAuthorizationError):
    """Report that the user denied a device request."""


class DeviceAuthorizationExpired(DeviceAuthorizationError):
    """Report that a device request exceeded its short lifetime."""


class DeviceCodeConsumed(DeviceAuthorizationError):
    """Report that a device code has already produced a token."""


class DevicePollingTooFast(DeviceAuthorizationError):
    """Report polling before the server-provided interval has elapsed."""

    def __init__(self, wait):
        """Record how many seconds the client should wait.

        Parameters
        ----------
        wait : int
            Minimum delay before another exchange attempt.
        """

        self.wait = wait
        super().__init__('The device authorization is being polled too quickly.')


class DeviceAuthorizationUnavailable(Exception):
    """Report a device request that can no longer be reviewed."""


def normalize_scopes(scopes):
    """Validate and deterministically order requested token scopes.

    Parameters
    ----------
    scopes : collections.abc.Iterable
        Requested public scope names.

    Returns
    -------
    list[str]
        Unique scope names in a stable order.

    Raises
    ------
    ValidationError
        If no valid scopes were requested.
    """

    if not isinstance(scopes, (list, tuple)) or not scopes or not all(isinstance(scope, str) for scope in scopes):
        raise ValidationError({'scopes': 'At least one valid scope is required.'})
    unknown_scopes = set(scopes) - ALLOWED_SCOPES
    if unknown_scopes:
        raise ValidationError({'scopes': 'One or more requested scopes are not supported.'})
    return sorted(set(scopes))


def access_token_allows_scope(access_token, required_scope):
    """Return whether a token grants a required operation scope.

    Parameters
    ----------
    access_token : accounts.models.AccessToken
        Authenticated token metadata.
    required_scope : str
        Public operation scope required by an endpoint.

    Returns
    -------
    bool
        Whether any stored scope grants the required scope.
    """

    return any(required_scope in SCOPE_IMPLICATIONS.get(granted_scope, ()) for granted_scope in access_token.scopes)


def create_access_token(*, user, name, scopes, origin, dataset=None, expires_at=None):
    """Create an access-token record and return its one-time secret.

    Parameters
    ----------
    user : accounts.models.User
        User whose current authorization constrains the token.
    name : str
        Human-readable dashboard label.
    scopes : collections.abc.Iterable
        Requested operation scopes.
    origin : str
        Issuance origin from ``AccessToken.Origin``.
    dataset : datasets.models.Dataset, optional
        Single immutable dataset boundary.
    expires_at : datetime.datetime, optional
        Earlier caller-selected expiry.

    Returns
    -------
    tuple[accounts.models.AccessToken, str]
        Persisted metadata and the complete token shown exactly once.

    Raises
    ------
    ValidationError
        If the label, scopes, origin, boundary, or expiry is invalid.
    """

    normalized_name = name.strip()
    if not normalized_name:
        raise ValidationError({'name': 'A token name is required.'})

    normalized_scopes = normalize_scopes(scopes)
    now = timezone.now()
    maximum_expiry = now + timedelta(days=settings.NIYAN_ACCESS_TOKEN_MAX_DAYS)
    selected_expiry = expires_at or maximum_expiry
    if timezone.is_naive(selected_expiry):
        raise ValidationError({'expires_at': 'Token expiry must include a timezone.'})
    if selected_expiry <= now or selected_expiry > maximum_expiry:
        raise ValidationError({'expires_at': 'Token expiry must be in the future and within the configured maximum lifetime.'})

    selector = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    raw_token = f'{TOKEN_PREFIX}{selector}_{secret}'
    access_token = AccessToken(
        user=user,
        name=normalized_name,
        origin=origin,
        resource_boundary=AccessToken.ResourceBoundary.DATASET if dataset is not None else AccessToken.ResourceBoundary.USER,
        dataset=dataset,
        scopes=normalized_scopes,
        selector=selector,
        secret_digest=_digest_secret(secret),
        expires_at=selected_expiry,
    )
    with transaction.atomic():
        access_token.full_clean()
        access_token.save()
        record_audit_event(
            action='access_token.issued',
            actor=user,
            access_token=access_token,
            scope=AuditEvent.Scope.TOKEN,
            dataset=dataset,
            token_id=access_token.id,
            payload={'origin': origin, 'resource_boundary': access_token.resource_boundary, 'scopes': normalized_scopes, 'expires_at': selected_expiry.isoformat()},
        )
    return access_token, raw_token


def authenticate_access_token(raw_token):
    """Validate an opaque access token and update its last-use metadata.

    Parameters
    ----------
    raw_token : str
        Complete bearer credential supplied by a client.

    Returns
    -------
    accounts.models.AccessToken
        Active token with its user and dataset loaded.

    Raises
    ------
    InvalidAccessToken
        If the token is malformed, unknown, revoked, expired, or owned by an inactive user.
    """

    selector, secret = _parse_access_token(raw_token)
    access_token = AccessToken.objects.select_related('user', 'dataset').filter(selector=selector).first()
    if access_token is None:
        raise InvalidAccessToken
    if not hmac.compare_digest(access_token.secret_digest, _digest_secret(secret)):
        record_audit_event(action='access_token.use_rejected', outcome=AuditEvent.Outcome.REJECTED, reason_code='invalid_secret', scope=AuditEvent.Scope.TOKEN, token_id=access_token.id)
        raise InvalidAccessToken
    now = timezone.now()
    if access_token.expires_at <= now:
        record_audit_event(
            action='access_token.expired',
            actor=access_token.user,
            access_token=access_token,
            scope=AuditEvent.Scope.TOKEN,
            dataset=access_token.dataset,
            token_id=access_token.id,
            deduplication_key=f'access-token-expired:{access_token.id}',
        )
        record_audit_event(action='access_token.use_rejected', outcome=AuditEvent.Outcome.REJECTED, reason_code='expired', actor=access_token.user, access_token=access_token, scope=AuditEvent.Scope.TOKEN, dataset=access_token.dataset, token_id=access_token.id)
        raise InvalidAccessToken
    if access_token.revoked_at is not None:
        record_audit_event(action='access_token.use_rejected', outcome=AuditEvent.Outcome.REJECTED, reason_code='revoked', actor=access_token.user, access_token=access_token, scope=AuditEvent.Scope.TOKEN, dataset=access_token.dataset, token_id=access_token.id)
        raise InvalidAccessToken
    if not access_token.user.is_active:
        record_audit_event(action='access_token.use_rejected', outcome=AuditEvent.Outcome.REJECTED, reason_code='inactive_user', actor=access_token.user, access_token=access_token, scope=AuditEvent.Scope.TOKEN, dataset=access_token.dataset, token_id=access_token.id)
        raise InvalidAccessToken

    used_at = now
    AccessToken.objects.filter(pk=access_token.pk).update(last_used_at=used_at)
    access_token.last_used_at = used_at
    return access_token


def revoke_access_token(*, access_token, revoked_by=None, authenticating_token=None):
    """Make an access token unusable while retaining its audit metadata.

    Parameters
    ----------
    access_token : accounts.models.AccessToken
        Token owned by the signed-in user.

    Returns
    -------
    accounts.models.AccessToken
        Revoked token metadata.
    """

    with transaction.atomic():
        locked_token = AccessToken.objects.select_for_update(of=('self',)).select_related('user', 'dataset__namespace').get(pk=access_token.pk)
        if locked_token.revoked_at is None:
            locked_token.revoked_at = timezone.now()
            locked_token.save(update_fields=['revoked_at'])
            record_audit_event(
                action='access_token.revoked',
                actor=revoked_by or locked_token.user,
                access_token=authenticating_token,
                scope=AuditEvent.Scope.TOKEN,
                dataset=locked_token.dataset,
                token_id=locked_token.id,
            )
        return locked_token


def start_device_authorization(*, name, scopes, requested_dataset_path=''):
    """Start a short-lived browser-assisted CLI authorization.

    Parameters
    ----------
    name : str
        Device label shown to the approving user.
    scopes : collections.abc.Iterable
        Scopes requested by the CLI.
    requested_dataset_path : str, optional
        Human-facing path for a requested dataset boundary.

    Returns
    -------
    tuple[accounts.models.DeviceAuthorization, str]
        Persisted request and its private device code.

    Raises
    ------
    ValidationError
        If the label, scopes, or dataset path is malformed.
    """

    normalized_name = name.strip()
    normalized_dataset_path = requested_dataset_path.strip().strip('/')
    if not normalized_name:
        raise ValidationError({'name': 'A device name is required.'})
    if requested_dataset_path and not normalized_dataset_path:
        raise ValidationError({'dataset_path': 'The requested dataset path is invalid.'})

    secret = secrets.token_urlsafe(32)
    authorization = DeviceAuthorization(
        device_secret_digest=_digest_secret(secret),
        user_code=_generate_unique_user_code(),
        name=normalized_name,
        requested_scopes=normalize_scopes(scopes),
        requested_dataset_path=normalized_dataset_path,
        expires_at=timezone.now() + timedelta(seconds=settings.NIYAN_DEVICE_AUTHORIZATION_LIFETIME_SECONDS),
    )
    authorization.full_clean()
    authorization.save()
    return authorization, f'{DEVICE_CODE_PREFIX}{authorization.id.hex}_{secret}'


def decide_device_authorization(*, user_code, user, approve):
    """Approve or deny a pending device authorization as a browser user.

    Parameters
    ----------
    user_code : str
        Human-readable one-time code shown by the CLI.
    user : accounts.models.User
        Browser-authenticated user making the decision.
    approve : bool
        Whether to grant the requested token.

    Returns
    -------
    accounts.models.DeviceAuthorization
        Updated authorization request.

    Raises
    ------
    DeviceAuthorizationUnavailable
        If the request is unknown, expired, or already decided.
    ValidationError
        If the requested dataset is not accessible to the approving user.
    """

    with transaction.atomic():
        authorization = DeviceAuthorization.objects.select_for_update().filter(user_code=_normalize_user_code(user_code)).first()
        if authorization is None or authorization.status != DeviceAuthorization.Status.PENDING or authorization.is_expired():
            raise DeviceAuthorizationUnavailable

        authorization.decided_at = timezone.now()
        if not approve:
            authorization.status = DeviceAuthorization.Status.DENIED
            authorization.save(update_fields=['status', 'decided_at'])
            return authorization

        dataset = None
        if authorization.requested_dataset_path:
            dataset = get_visible_dataset_by_path(dataset_path=authorization.requested_dataset_path, user=user)
            if dataset is None:
                raise ValidationError({'dataset_path': 'The requested dataset is unavailable.'})

        authorization.status = DeviceAuthorization.Status.APPROVED
        authorization.approved_by = user
        authorization.dataset = dataset
        authorization.save(update_fields=['status', 'approved_by', 'dataset', 'decided_at'])
        return authorization


def exchange_device_code(*, raw_device_code):
    """Exchange an approved private device code for a one-time token value.

    Parameters
    ----------
    raw_device_code : str
        Private device code held by the requesting CLI.

    Returns
    -------
    tuple[accounts.models.AccessToken, str]
        New CLI token metadata and its one-time secret.

    Raises
    ------
    DeviceAuthorizationError
        If the code is invalid, pending, denied, expired, consumed, or polled too quickly.
    ValidationError
        If an approved dataset is no longer accessible to the user.
    """

    authorization_id, secret = _parse_device_code(raw_device_code)
    authorization_pending = False
    with transaction.atomic():
        authorization = DeviceAuthorization.objects.select_for_update(of=('self',)).select_related('approved_by', 'dataset').filter(pk=authorization_id).first()
        if authorization is None or not hmac.compare_digest(authorization.device_secret_digest, _digest_secret(secret)):
            raise InvalidDeviceCode
        if authorization.is_expired():
            raise DeviceAuthorizationExpired
        if authorization.status == DeviceAuthorization.Status.DENIED:
            raise DeviceAuthorizationDenied
        if authorization.status == DeviceAuthorization.Status.CONSUMED:
            raise DeviceCodeConsumed
        if authorization.status == DeviceAuthorization.Status.PENDING:
            _record_device_poll(authorization)
            authorization_pending = True
        elif authorization.approved_by is None or not authorization.approved_by.is_active:
            raise DeviceAuthorizationDenied
        else:
            dataset = authorization.dataset
            if dataset is not None:
                dataset = get_visible_dataset(dataset_id=dataset.id, user=authorization.approved_by)
                if dataset is None:
                    raise ValidationError({'dataset_path': 'The approved dataset is no longer available.'})

            access_token, raw_token = create_access_token(
                user=authorization.approved_by,
                name=authorization.name,
                scopes=authorization.requested_scopes,
                origin=AccessToken.Origin.CLI,
                dataset=dataset,
            )
            authorization.status = DeviceAuthorization.Status.CONSUMED
            authorization.consumed_at = timezone.now()
            authorization.save(update_fields=['status', 'consumed_at'])
            return access_token, raw_token

    if authorization_pending:
        raise DeviceAuthorizationPending
    raise InvalidDeviceCode


def _digest_secret(secret):
    """Return a fixed-width SHA-256 digest for a high-entropy secret."""

    return hashlib.sha256(secret.encode()).hexdigest()


def _parse_access_token(raw_token):
    """Split a public access-token format into lookup and secret parts."""

    if not isinstance(raw_token, str) or len(raw_token) > 256 or not raw_token.startswith(TOKEN_PREFIX):
        raise InvalidAccessToken
    try:
        selector, secret = raw_token[len(TOKEN_PREFIX) :].split('_', 1)
    except ValueError as error:
        raise InvalidAccessToken from error
    if len(selector) != 16 or any(character not in string.hexdigits for character in selector) or not secret:
        raise InvalidAccessToken
    return selector.lower(), secret


def _parse_device_code(raw_device_code):
    """Split a private device code into its public identifier and secret."""

    if not isinstance(raw_device_code, str) or len(raw_device_code) > 320 or not raw_device_code.startswith(DEVICE_CODE_PREFIX):
        raise InvalidDeviceCode
    try:
        identifier, secret = raw_device_code[len(DEVICE_CODE_PREFIX) :].split('_', 1)
        authorization_id = UUID(hex=identifier)
    except (ValueError, AttributeError) as error:
        raise InvalidDeviceCode from error
    if not secret:
        raise InvalidDeviceCode
    return authorization_id, secret


def _normalize_user_code(user_code):
    """Normalize a human-readable device code for lookup."""

    return user_code.strip().upper()


def _generate_unique_user_code():
    """Create an unambiguous device code not currently in use."""

    for _ in range(10):
        value = ''.join(secrets.choice(USER_CODE_ALPHABET) for _ in range(8))
        user_code = f'{value[:4]}-{value[4:]}'
        if not DeviceAuthorization.objects.filter(user_code=user_code).exists():
            return user_code
    raise RuntimeError('Could not allocate a unique device authorization code.')


def _record_device_poll(authorization):
    """Persist a pending poll while enforcing the advertised interval."""

    now = timezone.now()
    interval = settings.NIYAN_DEVICE_AUTHORIZATION_POLL_INTERVAL_SECONDS
    if authorization.last_polled_at is not None:
        elapsed = (now - authorization.last_polled_at).total_seconds()
        if elapsed < interval:
            raise DevicePollingTooFast(max(1, int(interval - elapsed)))
    authorization.last_polled_at = now
    authorization.save(update_fields=['last_polled_at'])
