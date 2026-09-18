import base64
import binascii

from ninja.errors import AuthorizationError
from ninja.security import HttpBearer, SessionAuth

from accounts.models import AccessToken
from accounts.tokens import InvalidAccessToken, access_token_allows_scope, authenticate_access_token


class AccessTokenBearer(HttpBearer):
    """Authenticate Niyān access tokens sent with the Bearer scheme."""

    def authenticate(self, request, token):
        """Return the token owner for a valid bearer credential.

        Parameters
        ----------
        request : django.http.HttpRequest
            Request carrying the bearer credential.
        token : str
            Credential extracted by Django Ninja.

        Returns
        -------
        accounts.models.User or None
            Active token owner, or ``None`` for a rejected credential.
        """

        try:
            access_token = authenticate_access_token(token)
        except InvalidAccessToken:
            return None
        request.access_token = access_token
        return access_token.user


access_token_bearer = AccessTokenBearer()


class OptionalSessionAuth(SessionAuth):
    """Check CSRF only when a request actually presents a session cookie."""

    def _get_key(self, request):
        """Let later explicit auth schemes run when no session is present."""

        if self.param_name not in request.COOKIES:
            return None
        return super()._get_key(request)


session_or_access_token = [OptionalSessionAuth(), access_token_bearer]


def require_access(*, request, scope, dataset_id=None):
    """Enforce token scope and resource boundary for an API operation.

    Browser sessions have no credential-level restriction and continue to the
    domain authorization layer. Bearer tokens are restricted by both scope and
    their optional immutable dataset boundary.

    Parameters
    ----------
    request : django.http.HttpRequest
        Authenticated request.
    scope : str
        Required operation scope.
    dataset_id : uuid.UUID, optional
        Dataset targeted by the operation.

    Raises
    ------
    AuthorizationError
        If a bearer token lacks the scope or targets another resource.
    """

    access_token = getattr(request, 'access_token', None)
    if access_token is None:
        return
    if not access_token_allows_scope(access_token, scope):
        raise AuthorizationError
    if access_token.resource_boundary == AccessToken.ResourceBoundary.DATASET and access_token.dataset_id != dataset_id:
        raise AuthorizationError


def get_access_token(request):
    """Return bearer-token metadata when the current request used it."""

    return getattr(request, 'access_token', None)


def authenticate_git_basic(request):
    """Authenticate an access token supplied as an HTTPS Basic password.

    Parameters
    ----------
    request : django.http.HttpRequest
        Smart-HTTP request carrying a Basic authorization header.

    Returns
    -------
    accounts.models.AccessToken or None
        Active access token, or ``None`` for malformed or rejected credentials.
    """

    authorization = request.headers.get('Authorization', '')
    scheme, separator, encoded = authorization.partition(' ')
    if not separator or scheme.lower() != 'basic' or not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded, validate=True).decode('utf-8')
        _, password = decoded.split(':', 1)
        return authenticate_access_token(password)
    except (binascii.Error, UnicodeDecodeError, ValueError, InvalidAccessToken):
        return None


def access_token_permits(*, access_token, scope, dataset_id):
    """Return whether token scope and resource boundary permit a dataset.

    Parameters
    ----------
    access_token : accounts.models.AccessToken
        Authenticated credential metadata.
    scope : str
        Required operation scope.
    dataset_id : uuid.UUID
        Immutable dataset being accessed.

    Returns
    -------
    bool
        Whether credential-level restrictions permit the operation.
    """

    if not access_token_allows_scope(access_token, scope):
        return False
    return access_token.resource_boundary == AccessToken.ResourceBoundary.USER or access_token.dataset_id == dataset_id
