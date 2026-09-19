from datetime import datetime
from typing import Any
from uuid import UUID

from django.db.models import Q
from ninja import Query, Router, Schema, Status
from ninja.security import django_auth

from accounts.authentication import require_access, session_or_access_token
from datasets.models import AuditEvent
from datasets.policies import get_dataset_role
from datasets.selectors import get_visible_dataset
from namespaces.models import NamespaceMembership
from namespaces.selectors import get_manageable_group


class ErrorResponse(Schema):
    """Provide a stable audit-query error."""

    code: str
    detail: str


class AuditEventResponse(Schema):
    """Expose one non-secret immutable event."""

    id: UUID
    occurred_at: datetime
    action: str
    outcome: str
    reason_code: str
    request_id: UUID | None
    actor_user_id: int | None
    actor_username: str
    access_token_id: UUID | None
    scope: str
    group_id: UUID | None
    group_path: str
    dataset_id: UUID | None
    dataset_path: str
    ref_name: str
    token_id: UUID | None
    draft_id: UUID | None
    payload_version: int
    payload: dict[str, Any]


class AuditEventListResponse(Schema):
    """Return deterministic reverse-chronological audit pagination."""

    count: int
    limit: int
    offset: int
    items: list[AuditEventResponse]


dataset_router = Router(tags=['audit events'], auth=session_or_access_token)
group_router = Router(tags=['audit events'], auth=session_or_access_token)
account_router = Router(tags=['audit events'])


def serialize_audit_event(event):
    """Convert one retained event to the explicitly safe public shape."""

    return {
        'id': event.id,
        'occurred_at': event.occurred_at,
        'action': event.action,
        'outcome': event.outcome,
        'reason_code': event.reason_code,
        'request_id': event.request_id,
        'actor_user_id': event.actor_user_id,
        'actor_username': event.actor_username,
        'access_token_id': event.access_token_id,
        'scope': event.scope,
        'group_id': event.group_id,
        'group_path': event.group_path,
        'dataset_id': event.dataset_id,
        'dataset_path': event.dataset_path,
        'ref_name': event.ref_name,
        'token_id': event.token_id,
        'draft_id': event.draft_id,
        'payload_version': event.payload_version,
        'payload': event.payload,
    }


def _page(queryset, *, limit, offset):
    """Serialize one stable limit-and-offset page."""

    count = queryset.count()
    items = queryset.order_by('-occurred_at', '-id')[offset : offset + limit]
    return {'count': count, 'limit': limit, 'offset': offset, 'items': [serialize_audit_event(event) for event in items]}


@dataset_router.get('/{dataset_id}/audit-events', response={200: AuditEventListResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse})
def list_dataset_audit_events(request, dataset_id: UUID, limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0, le=10000)):
    """List events for a dataset currently owned by the requester."""

    require_access(request=request, scope='read_api', dataset_id=dataset_id)
    dataset = get_visible_dataset(dataset_id=dataset_id, user=request.auth)
    if dataset is None:
        return Status(404, {'code': 'dataset_not_found', 'detail': 'The requested dataset does not exist.'})
    if get_dataset_role(user=request.auth, dataset=dataset) != NamespaceMembership.Role.OWNER:
        return Status(403, {'code': 'permission_denied', 'detail': 'Only dataset owners can inspect audit events.'})
    return _page(AuditEvent.objects.filter(dataset_id=dataset.id), limit=limit, offset=offset)


@group_router.get('/{namespace_id}/audit-events', response={200: AuditEventListResponse, 401: ErrorResponse, 403: ErrorResponse, 404: ErrorResponse})
def list_group_audit_events(request, namespace_id: UUID, limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0, le=10000)):
    """List identity and direct-membership events for an owned group."""

    require_access(request=request, scope='read_api')
    group = get_manageable_group(namespace_id=namespace_id, user=request.auth)
    if group is None:
        return Status(404, {'code': 'group_not_found', 'detail': 'The requested group does not exist.'})
    return _page(AuditEvent.objects.filter(group_id=group.id, action__startswith='group.'), limit=limit, offset=offset)


@account_router.get('/audit-events', auth=django_auth, response={200: AuditEventListResponse, 401: ErrorResponse})
def list_own_token_audit_events(request, limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0, le=10000)):
    """List the signed-in user's own access-token lifecycle."""

    owned_token_ids = request.auth.access_tokens.values_list('id', flat=True)
    events = AuditEvent.objects.filter(scope=AuditEvent.Scope.TOKEN, action__startswith='access_token.').filter(Q(actor_user_id=request.auth.pk) | Q(token_id__in=owned_token_ids))
    return _page(events, limit=limit, offset=offset)
