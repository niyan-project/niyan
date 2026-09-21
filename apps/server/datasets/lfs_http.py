import json
import re

from django.conf import settings
from django.core import signing
from django.db import IntegrityError
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt

from accounts.authentication import access_token_permits, authenticate_git_basic
from accounts.models import AccessToken
from datasets import lfs_transfers
from datasets.lfs_transfers import LfsIntegrityError, LfsObjectMissing, LfsTransferUnavailable, finalize_lfs_upload, issue_download_action, issue_upload_action
from datasets.models import Dataset, LfsObject
from datasets.object_storage import ObjectStoreError
from datasets.policies import can_read_dataset, can_write_repository


LFS_CONTENT_TYPE = 'application/vnd.git-lfs+json'
MAX_BATCH_BODY_BYTES = 1024 * 1024
MAX_BATCH_OBJECTS = 100
MAX_LFS_OBJECT_SIZE = 2**63 - 1
MAX_S3_OBJECT_SIZE = 5 * 1024**4
MULTIPART_TRANSFER = 'niyan-multipart'
OID_PATTERN = re.compile(r'^[0-9a-f]{64}$')
LFS_VERIFY_AUTHORIZATION_SALT = 'niyan.lfs.verify.v1'


class InvalidLfsBatch(ValueError):
    """Report a malformed or unsupported Git LFS batch request."""

    def __init__(self, message, *, status=422):
        """Attach the protocol status appropriate for the rejected metadata."""

        super().__init__(message)
        self.status = status


@csrf_exempt
def lfs_batch(request, dataset_id):
    """Negotiate standard Git LFS basic transfers for one dataset.

    Parameters
    ----------
    request : django.http.HttpRequest
        Incoming Git LFS Batch API request.
    dataset_id : uuid.UUID
        Immutable dataset identity taken from trusted routing.

    Returns
    -------
    django.http.JsonResponse
        Standard Git LFS batch response or sanitized protocol error.
    """

    if request.method != 'POST':
        response = _lfs_response({'message': 'Only POST is supported.'}, status=405)
        response['Allow'] = 'POST'
        return response

    access_token = authenticate_git_basic(request)
    if access_token is None:
        response = _lfs_response({'message': 'Authentication is required.'}, status=401)
        response['WWW-Authenticate'] = 'Basic realm="Niyan Git LFS"'
        return response

    try:
        payload = _parse_batch_request(request)
    except InvalidLfsBatch as error:
        return _lfs_response({'message': str(error)}, status=error.status)

    operation = payload['operation']
    required_scope = 'write_repository' if operation == 'upload' else 'read_repository'
    if not access_token_permits(access_token=access_token, scope=required_scope, dataset_id=dataset_id):
        return _lfs_response({'message': 'The credential does not permit this operation.'}, status=403)

    dataset = Dataset.objects.select_related('namespace__parent').filter(pk=dataset_id, deletion_started_at__isnull=True).first()
    if dataset is None:
        allowed = False
    elif operation == 'upload':
        allowed = can_write_repository(user=access_token.user, dataset=dataset)
    else:
        allowed = can_read_dataset(user=access_token.user, dataset=dataset)
    if not allowed:
        return _lfs_response({'message': 'Dataset repository not found.'}, status=404)

    try:
        transfer = _select_transfer(payload)
    except InvalidLfsBatch as error:
        return _lfs_response({'message': str(error)}, status=error.status)
    # Resolve through the transfer module so its adapter boundary remains one
    # shared injection seam for tests and alternate deployments.
    object_store = lfs_transfers.S3ObjectStore(settings.NIYAN_S3_CONFIGURATION)
    objects = [
        _negotiate_object(
            dataset=dataset,
            access_token=access_token,
            operation=operation,
            requested=requested,
            transfer=transfer,
            verify_url=request.build_absolute_uri(reverse('git-lfs-verify', kwargs={'dataset_id': dataset.id, 'oid': requested['oid']})),
            multipart_url=request.build_absolute_uri(f'/api/v1/datasets/{dataset.id}/lfs/objects/{requested["oid"]}/multipart'),
            object_store=object_store,
        )
        for requested in payload['objects']
    ]
    return _lfs_response({'transfer': transfer, 'hash_algo': 'sha256', 'objects': objects})


@csrf_exempt
def lfs_verify(request, dataset_id, oid):
    """Finalize one basic Git LFS upload after checking stored metadata."""

    if request.method != 'POST':
        response = _lfs_response({'message': 'Only POST is supported.'}, status=405)
        response['Allow'] = 'POST'
        return response
    access_token, verification_claims = _authenticate_verify_request(request=request, dataset_id=dataset_id, oid=oid)
    if access_token is None:
        response = _lfs_response({'message': 'Authentication is required.'}, status=401)
        response['WWW-Authenticate'] = 'Basic realm="Niyan Git LFS"'
        return response
    if not access_token_permits(access_token=access_token, scope='write_repository', dataset_id=dataset_id):
        return _lfs_response({'message': 'The credential does not permit this operation.'}, status=403)
    dataset = Dataset.objects.select_related('namespace__parent').filter(pk=dataset_id, deletion_started_at__isnull=True).first()
    if dataset is None or not can_write_repository(user=access_token.user, dataset=dataset):
        return _lfs_response({'message': 'Dataset repository not found.'}, status=404)
    try:
        requested = _parse_verify_request(request, route_oid=oid)
    except InvalidLfsBatch as error:
        return _lfs_response({'message': str(error)}, status=error.status)
    if verification_claims is not None and verification_claims['size'] != requested['size']:
        return _lfs_response({'message': 'The verification object size is invalid.'}, status=422)
    lfs_object = LfsObject.objects.select_related('dataset').filter(dataset=dataset, oid=oid).first()
    if lfs_object is None:
        return _lfs_response({'message': 'The Git LFS object is unavailable.'}, status=404)
    if lfs_object.size != requested['size']:
        return _lfs_response({'message': 'The declared object size conflicts with existing metadata.'}, status=422)
    try:
        finalized = finalize_lfs_upload(lfs_object=lfs_object)
    except LfsObjectMissing:
        return _lfs_response({'message': 'The uploaded Git LFS object is unavailable.'}, status=404)
    except LfsIntegrityError as error:
        return _lfs_response({'message': str(error)}, status=422)
    except (LfsTransferUnavailable, ObjectStoreError):
        return _lfs_response({'message': 'Upload verification is temporarily unavailable.'}, status=503)
    return _lfs_response({'oid': finalized.oid, 'size': finalized.size})


def _parse_batch_request(request):
    """Parse and validate bounded Git LFS batch metadata.

    Raises
    ------
    InvalidLfsBatch
        If the request cannot be processed as a standard basic batch.
    """

    if request.content_type != LFS_CONTENT_TYPE:
        raise InvalidLfsBatch(f'Content-Type must be {LFS_CONTENT_TYPE}.')
    content_length = request.META.get('CONTENT_LENGTH')
    if content_length:
        try:
            if int(content_length) > MAX_BATCH_BODY_BYTES:
                raise InvalidLfsBatch('The batch request body is too large.', status=413)
        except ValueError as error:
            raise InvalidLfsBatch('The Content-Length header is invalid.') from error
    body = request.body
    if len(body) > MAX_BATCH_BODY_BYTES:
        raise InvalidLfsBatch('The batch request body is too large.', status=413)
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise InvalidLfsBatch('The request body must contain valid JSON.') from error
    if not isinstance(payload, dict):
        raise InvalidLfsBatch('The batch request must be a JSON object.')

    operation = payload.get('operation')
    if operation not in {'upload', 'download'}:
        raise InvalidLfsBatch('The batch operation must be upload or download.')
    if payload.get('hash_algo', 'sha256') != 'sha256':
        raise InvalidLfsBatch('Niyān supports only the sha256 Git LFS hash algorithm.')
    transfers = payload.get('transfers', ['basic'])
    if not isinstance(transfers, list) or not transfers or any(not isinstance(transfer, str) for transfer in transfers):
        raise InvalidLfsBatch('The transfers field must be a non-empty list of names.')
    if not {'basic', MULTIPART_TRANSFER}.intersection(transfers):
        raise InvalidLfsBatch('The client did not advertise a supported Git LFS transfer adapter.')

    objects = payload.get('objects')
    if not isinstance(objects, list):
        raise InvalidLfsBatch('The objects field must be a list.')
    if len(objects) > MAX_BATCH_OBJECTS:
        raise InvalidLfsBatch('A batch may contain at most 100 objects.', status=413)
    normalized_objects = []
    for requested in objects:
        if not isinstance(requested, dict):
            raise InvalidLfsBatch('Every batch object must be a JSON object.')
        oid = requested.get('oid')
        size = requested.get('size')
        if not isinstance(oid, str) or not OID_PATTERN.fullmatch(oid):
            raise InvalidLfsBatch('Every object must use a lowercase 64-character SHA-256 identifier.')
        if isinstance(size, bool) or not isinstance(size, int) or size < 0 or size > MAX_LFS_OBJECT_SIZE:
            raise InvalidLfsBatch('Every object size must be a non-negative integer.')
        normalized_objects.append({'oid': oid, 'size': size})
    return {'operation': operation, 'objects': normalized_objects, 'transfers': transfers}


def _select_transfer(payload):
    """Select a client-advertised transfer according to object sizes."""

    transfers = payload['transfers']
    if payload['operation'] == 'download':
        if 'basic' not in transfers:
            raise InvalidLfsBatch('Downloads require the basic Git LFS transfer adapter.')
        return 'basic'
    requires_multipart = any(requested['size'] >= settings.NIYAN_LFS_MULTIPART_THRESHOLD_BYTES for requested in payload['objects'])
    if requires_multipart and MULTIPART_TRANSFER in transfers:
        return MULTIPART_TRANSFER
    if 'basic' in transfers:
        return 'basic'
    if MULTIPART_TRANSFER in transfers:
        return MULTIPART_TRANSFER
    raise InvalidLfsBatch('The client did not advertise a supported upload transfer adapter.')


def _parse_verify_request(request, *, route_oid):
    """Parse the bounded standard Git LFS verification request body."""

    if request.content_type != LFS_CONTENT_TYPE:
        raise InvalidLfsBatch(f'Content-Type must be {LFS_CONTENT_TYPE}.')
    content_length = request.META.get('CONTENT_LENGTH')
    if content_length:
        try:
            if int(content_length) > 16 * 1024:
                raise InvalidLfsBatch('The verification request body is too large.', status=413)
        except ValueError as error:
            raise InvalidLfsBatch('The Content-Length header is invalid.') from error
    body = request.body
    if len(body) > 16 * 1024:
        raise InvalidLfsBatch('The verification request body is too large.', status=413)
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise InvalidLfsBatch('The request body must contain valid JSON.') from error
    if not OID_PATTERN.fullmatch(route_oid) or not isinstance(payload, dict) or payload.get('oid') != route_oid:
        raise InvalidLfsBatch('The verification object identifier is invalid.')
    size = payload.get('size')
    if isinstance(size, bool) or not isinstance(size, int) or size < 0 or size > MAX_LFS_OBJECT_SIZE:
        raise InvalidLfsBatch('The verification object size is invalid.')
    return {'size': size}


def _negotiate_object(*, dataset, access_token, operation, requested, transfer, verify_url, multipart_url, object_store):
    """Negotiate one object without exposing storage or policy internals."""

    oid = requested['oid']
    size = requested['size']
    response = {'oid': oid, 'size': size, 'authenticated': True}
    if operation == 'upload':
        return _negotiate_upload(dataset=dataset, access_token=access_token, oid=oid, size=size, response=response, transfer=transfer, verify_url=verify_url, multipart_url=multipart_url, object_store=object_store)
    return _negotiate_download(dataset=dataset, oid=oid, size=size, response=response, object_store=object_store)


def _negotiate_upload(*, dataset, access_token, oid, size, response, transfer, verify_url, multipart_url, object_store):
    """Reuse verified content or authorize one bounded basic upload."""

    try:
        lfs_object, _ = LfsObject.objects.get_or_create(dataset=dataset, oid=oid, defaults={'size': size})
    except IntegrityError:
        lfs_object = LfsObject.objects.get(dataset=dataset, oid=oid)
    if lfs_object.size != size:
        return _object_error(response, 422, 'The declared object size conflicts with existing metadata.')
    if lfs_object.state in {LfsObject.State.AVAILABLE, LfsObject.State.REFERENCED}:
        return response
    if size > MAX_S3_OBJECT_SIZE:
        return _object_error(response, 422, 'This object exceeds the supported S3 object size.')
    verify_action = _issue_verify_action(access_token=access_token, dataset=dataset, oid=oid, size=size, verify_url=verify_url)
    if size >= settings.NIYAN_LFS_MULTIPART_THRESHOLD_BYTES:
        if transfer != MULTIPART_TRANSFER:
            return _object_error(response, 422, 'This object requires the Niyān multipart transfer agent.')
        response['actions'] = {'upload': {'href': multipart_url}, 'verify': verify_action}
        return response
    try:
        action = issue_upload_action(lfs_object=lfs_object, object_store=object_store)
    except (LfsTransferUnavailable, ObjectStoreError):
        return _object_error(response, 503, 'The upload action is temporarily unavailable.')
    response['actions'] = {'upload': _serialize_action(action), 'verify': verify_action}
    return response


def _issue_verify_action(*, access_token, dataset, oid, size, verify_url):
    """Issue a short-lived capability for Git LFS's separate verify request."""

    capability = signing.dumps(
        {'token_id': str(access_token.id), 'dataset_id': str(dataset.id), 'oid': oid, 'size': size},
        salt=LFS_VERIFY_AUTHORIZATION_SALT,
        compress=True,
    )
    return {
        'href': verify_url,
        'expires_in': settings.NIYAN_LFS_TRANSFER_ACTION_LIFETIME_SECONDS,
        'header': {'Authorization': f'Bearer {capability}'},
    }


def _authenticate_verify_request(*, request, dataset_id, oid):
    """Authenticate Basic credentials or one exact short-lived verify capability."""

    access_token = authenticate_git_basic(request)
    if access_token is not None:
        return access_token, None

    authorization = request.headers.get('Authorization', '')
    scheme, separator, capability = authorization.partition(' ')
    if not separator or scheme.lower() != 'bearer' or not capability:
        return None, None
    try:
        claims = signing.loads(
            capability,
            salt=LFS_VERIFY_AUTHORIZATION_SALT,
            max_age=settings.NIYAN_LFS_TRANSFER_ACTION_LIFETIME_SECONDS,
        )
    except signing.BadSignature:
        return None, None
    if not isinstance(claims, dict) or claims.get('dataset_id') != str(dataset_id) or claims.get('oid') != oid or isinstance(claims.get('size'), bool) or not isinstance(claims.get('size'), int):
        return None, None
    access_token = AccessToken.objects.select_related('user', 'dataset').filter(pk=claims.get('token_id')).first()
    if access_token is None or not access_token.user.is_active or not access_token.is_active():
        return None, None
    return access_token, claims


def _negotiate_download(*, dataset, oid, size, response, object_store):
    """Authorize a direct download only for finalized matching content."""

    lfs_object = LfsObject.objects.filter(dataset=dataset, oid=oid).first()
    if lfs_object is None or lfs_object.state == LfsObject.State.PENDING:
        return _object_error(response, 404, 'The Git LFS object is unavailable.')
    if lfs_object.size != size:
        return _object_error(response, 422, 'The declared object size conflicts with existing metadata.')
    try:
        action = issue_download_action(lfs_object=lfs_object, object_store=object_store)
    except (LfsTransferUnavailable, ObjectStoreError):
        return _object_error(response, 503, 'The download action is temporarily unavailable.')
    response['actions'] = {'download': _serialize_action(action)}
    return response


def _serialize_action(action):
    """Return the standard Git LFS representation of one signed action."""

    serialized = {'href': action.url, 'expires_in': action.expires_in}
    if action.headers:
        serialized['header'] = action.headers
    return serialized


def _object_error(response, code, message):
    """Attach one standard per-object Git LFS error."""

    response['error'] = {'code': code, 'message': message}
    return response


def _lfs_response(payload, *, status=200):
    """Return JSON with the media type required by Git LFS clients."""

    return JsonResponse(payload, status=status, content_type=LFS_CONTENT_TYPE)
