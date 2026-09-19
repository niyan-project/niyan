import json
from collections.abc import Mapping

from django.core.exceptions import ValidationError

from datasets.models import AuditEvent


MAX_PAYLOAD_BYTES = 16 * 1024
MAX_PAYLOAD_DEPTH = 5
MAX_PAYLOAD_ITEMS = 200
MAX_PAYLOAD_STRING = 4096
FORBIDDEN_KEY_PARTS = (
    'authorization',
    'commit_message',
    'content',
    'password',
    'provider_response',
    'push_context',
    'raw_token',
    'secret',
    'session',
    'signed_url',
    'storage_key',
    'traceback',
)


def record_audit_event(*, action, outcome=AuditEvent.Outcome.ACCEPTED, reason_code='', actor=None, access_token=None, request_id=None, scope, group=None, dataset=None, ref_name='', token_id=None, draft_id=None, payload=None, deduplication_key=None):
    """Append one bounded event without retaining relational dependencies.

    Parameters
    ----------
    action : str
        Stable dotted public action name.
    outcome : str, optional
        Accepted or rejected outcome.
    reason_code : str, optional
        Bounded public reason identifier, never raw exception text.
    actor : accounts.models.User, optional
        Authenticated actor when one is known.
    access_token : accounts.models.AccessToken, optional
        Credential metadata, never its secret.
    request_id : uuid.UUID, optional
        Correlation identifier already safe for disclosure.
    scope : str
        Product surface from ``AuditEvent.Scope``.
    group : namespaces.models.Namespace, optional
        Group whose immutable ID and current path should be snapshotted.
    dataset : datasets.models.Dataset, optional
        Dataset whose immutable ID and current path should be snapshotted.
    ref_name : str, optional
        Full Git ref affected by this action.
    token_id : uuid.UUID, optional
        Access-token record whose lifecycle changed.
    draft_id : uuid.UUID, optional
        Browser-draft record involved in the action.
    payload : dict, optional
        Version-one non-secret action metadata.
    deduplication_key : str, optional
        Stable internal identity for reconciliation-created events.

    Returns
    -------
    datasets.models.AuditEvent
        Newly inserted or previously reconciled immutable event.
    """

    normalized_payload = dict(payload or {})
    _validate_payload(normalized_payload)
    values = {
        'action': action,
        'outcome': outcome,
        'reason_code': reason_code,
        'request_id': request_id,
        'actor_user_id': getattr(actor, 'pk', None),
        'actor_username': getattr(actor, 'username', ''),
        'access_token_id': getattr(access_token, 'pk', None),
        'scope': scope,
        'group_id': getattr(group, 'pk', None),
        'group_path': getattr(group, 'path', ''),
        'dataset_id': getattr(dataset, 'pk', None),
        'dataset_path': getattr(dataset, 'path', ''),
        'ref_name': ref_name,
        'token_id': token_id,
        'draft_id': draft_id,
        'payload': normalized_payload,
    }
    if deduplication_key is None:
        return AuditEvent.objects.create(**values)
    event, _ = AuditEvent.objects.get_or_create(deduplication_key=deduplication_key, defaults=values)
    return event


def record_expired_access_tokens(*, now, batch_size=100):
    """Materialize time-based token expiry events in bounded worker passes.

    Parameters
    ----------
    now : datetime.datetime
        Worker clock used to identify expired tokens.
    batch_size : int, optional
        Maximum previously unaudited expiries to append.

    Returns
    -------
    int
        Number of expiry events created or reconciled.
    """

    from accounts.models import AccessToken

    audited_token_ids = AuditEvent.objects.filter(action='access_token.expired', token_id__isnull=False).values_list('token_id', flat=True)
    tokens = AccessToken.objects.select_related('user', 'dataset__namespace').filter(expires_at__lte=now).exclude(id__in=audited_token_ids).order_by('expires_at', 'id')[:batch_size]
    count = 0
    for access_token in tokens:
        record_audit_event(
            action='access_token.expired',
            actor=access_token.user,
            access_token=access_token,
            scope=AuditEvent.Scope.TOKEN,
            dataset=access_token.dataset,
            token_id=access_token.id,
            deduplication_key=f'access-token-expired:{access_token.id}',
        )
        count += 1
    return count


def _validate_payload(payload):
    """Reject oversized, deeply nested, or suspicious audit metadata."""

    if not isinstance(payload, Mapping):
        raise ValidationError('Audit payloads must be JSON objects.')
    item_count = 0

    def visit(value, *, depth, key=''):
        nonlocal item_count
        if depth > MAX_PAYLOAD_DEPTH:
            raise ValidationError('Audit payloads are too deeply nested.')
        item_count += 1
        if item_count > MAX_PAYLOAD_ITEMS:
            raise ValidationError('Audit payloads contain too many values.')
        if key and any(part in key.casefold() for part in FORBIDDEN_KEY_PARTS):
            raise ValidationError('Audit payloads contain a forbidden field.')
        if isinstance(value, Mapping):
            for child_key, child_value in value.items():
                if not isinstance(child_key, str) or len(child_key) > 100:
                    raise ValidationError('Audit payload keys must be bounded strings.')
                visit(child_value, depth=depth + 1, key=child_key)
        elif isinstance(value, (list, tuple)):
            for child_value in value:
                visit(child_value, depth=depth + 1)
        elif isinstance(value, str):
            if len(value) > MAX_PAYLOAD_STRING:
                raise ValidationError('Audit payload strings are too long.')
        elif value is not None and not isinstance(value, (bool, int, float)):
            raise ValidationError('Audit payload values must be JSON-compatible.')

    visit(payload, depth=0)
    if len(json.dumps(payload, separators=(',', ':'), sort_keys=True).encode()) > MAX_PAYLOAD_BYTES:
        raise ValidationError('Audit payloads are too large.')
