from ninja import NinjaAPI, Schema
from ninja.errors import AuthenticationError, AuthorizationError, Throttled, ValidationError

from accounts.api import router as authentication_router
from accounts.system_api import router as system_router
from datasets.api import router as datasets_router
from datasets.audit_api import account_router as account_audit_router
from datasets.audit_api import dataset_router as dataset_audit_router
from datasets.audit_api import group_router as group_audit_router
from datasets.browser_commit_api import router as browser_commit_router
from datasets.lfs_api import router as lfs_router
from datasets.repository_api import router as repository_router
from namespaces.api import router as namespaces_router


api = NinjaAPI(title='Niyān API', version='1.0.0', urls_namespace='api-v1')
api.add_router('/auth', authentication_router)
api.add_router('/auth', account_audit_router)
api.add_router('/system', system_router)
api.add_router('/datasets', datasets_router)
api.add_router('/datasets', dataset_audit_router)
api.add_router('/datasets', browser_commit_router)
api.add_router('/datasets', repository_router)
api.add_router('/datasets', lfs_router)
api.add_router('/namespaces', namespaces_router)
api.add_router('/namespaces', group_audit_router)


class FileSystemCapabilities(Schema):
    """Describe the supported Niyān filesystem protocol surface."""

    protocol_version: int
    features: list[str]


class CapabilitiesResponse(Schema):
    """Advertise stable API and filesystem compatibility identifiers."""

    api_versions: list[str]
    filesystem: FileSystemCapabilities


@api.get('/capabilities', auth=None, response=CapabilitiesResponse, tags=['capabilities'])
def get_capabilities(request):
    """Return inexpensive public compatibility metadata for clients."""

    return {
        'api_versions': ['v1'],
        'filesystem': {
            'protocol_version': 1,
            'features': [
                'dataset-path-resolution',
                'exact-revision-resolution',
                'repository-metadata',
                'git-blob-reads',
                'authorized-lfs-download-actions',
            ],
        },
    }


@api.exception_handler(AuthenticationError)
def handle_authentication_error(request, error):
    """Return the public error shape when session authentication fails.

    Parameters
    ----------
    request : django.http.HttpRequest
        Request rejected before reaching an API operation.
    error : ninja.errors.AuthenticationError
        Authentication failure produced by Django Ninja.

    Returns
    -------
    django.http.HttpResponse
        Stable unauthorized response for API clients.
    """

    return api.create_response(request, {'code': 'authentication_required', 'detail': 'Authentication is required.'}, status=401)


@api.exception_handler(AuthorizationError)
def handle_authorization_error(request, error):
    """Return the public error shape for a credential restriction.

    Parameters
    ----------
    request : django.http.HttpRequest
        Authenticated request rejected before an API operation completes.
    error : ninja.errors.AuthorizationError
        Authorization failure produced by the credential policy.

    Returns
    -------
    django.http.HttpResponse
        Stable forbidden response for API clients.
    """

    return api.create_response(request, {'code': 'permission_denied', 'detail': 'The credential does not permit this operation.'}, status=403)


@api.exception_handler(Throttled)
def handle_throttled_error(request, error):
    """Return a stable response when an authentication endpoint is rate-limited."""

    response = api.create_response(request, {'code': 'rate_limited', 'detail': 'Too many requests.'}, status=429)
    if error.wait is not None:
        response['Retry-After'] = str(max(1, round(error.wait)))
    return response


@api.exception_handler(ValidationError)
def handle_request_validation_error(request, error):
    """Return a stable public shape for request validation failures.

    Parameters
    ----------
    request : django.http.HttpRequest
        Request rejected before reaching an API operation.
    error : ninja.errors.ValidationError
        Validation failure produced by Django Ninja.

    Returns
    -------
    django.http.HttpResponse
        JSON response without Pydantic or server implementation details.
    """

    return api.create_response(request, {'code': 'validation_error', 'detail': 'The request data is invalid.'}, status=422)
