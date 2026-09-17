from ninja import NinjaAPI
from ninja.errors import AuthenticationError, ValidationError

from datasets.api import router as datasets_router


api = NinjaAPI(title='Niyān API', version='1.0.0', urls_namespace='api-v1')
api.add_router('/datasets', datasets_router)


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
