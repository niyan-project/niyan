from uuid import UUID

from ninja import Query, Router, Schema, Status

from accounts.authentication import require_access, session_or_access_token
from namespaces.selectors import get_visible_namespace_by_path


class NamespaceResponse(Schema):
    """Expose namespace identity needed by path-based API clients."""

    id: UUID
    path: str
    name: str
    kind: str


class ErrorResponse(Schema):
    """Provide a stable namespace lookup error."""

    code: str
    detail: str


router = Router(tags=['namespaces'], auth=session_or_access_token)


@router.get('/resolve', response={200: NamespaceResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse, 422: ErrorResponse})
def resolve_namespace_endpoint(request, path: str = Query(..., min_length=1, max_length=2048)):
    """Resolve one visible human-facing namespace path."""

    require_access(request=request, scope='read_api')
    namespace = get_visible_namespace_by_path(namespace_path=path, user=request.auth)
    if namespace is None:
        return Status(404, {'code': 'namespace_not_found', 'detail': 'The requested namespace does not exist.'})
    return {'id': namespace.id, 'path': namespace.path, 'name': namespace.name, 'kind': namespace.kind}
