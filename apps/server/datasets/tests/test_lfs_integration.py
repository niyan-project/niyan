import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import Request, urlopen
from unittest.mock import patch

from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import AccessToken, User
from accounts.tokens import create_access_token
from datasets.models import Dataset, DatasetGrant, LfsObject
from datasets.object_storage import ObjectNotFound, ObjectStoreError, PresignedAction, StoredObject
from namespaces.models import NamespaceMembership


class DirectObjectHandler(BaseHTTPRequestHandler):
    """Receive test object bytes outside Django's request stack."""

    def do_PUT(self):
        """Store one body only when its latest opaque action is presented."""

        path, generation = self._request_identity()
        if generation != self.server.valid_generations.get(path):
            self.send_error(403)
            return
        length = int(self.headers.get('Content-Length', '-1'))
        if length < 0:
            self.send_error(411)
            return
        self.server.objects[path] = self.rfile.read(length)
        self.server.put_paths.append(path)
        self.send_response(200)
        self.send_header('ETag', '"integration-etag"')
        self.end_headers()

    def do_GET(self):
        """Return one stored body through the test provider, not Django."""

        path = urlsplit(self.path).path
        content = self.server.objects.get(path)
        if content is None:
            self.send_error(404)
            return
        self.server.get_paths.append(path)
        self.send_response(200)
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        """Keep deterministic tests from writing request diagnostics."""

    def _request_identity(self):
        """Return the object path and opaque action generation."""

        parsed = urlsplit(self.path)
        values = parse_qs(parsed.query).get('action', [])
        generation = int(values[0]) if len(values) == 1 and values[0].isdigit() else None
        return parsed.path, generation


class NetworkedMemoryStore:
    """Sign actions for a separate in-memory HTTP object service."""

    supports_sha256_checksums = True

    def __init__(self, server):
        """Bind the adapter to one isolated provider server."""

        self.server = server
        self.generation = 0
        self.fail_signing = False
        self.fail_head = False

    def presign_upload(self, relative_key, *, size, expires_in, checksum_sha256=None):
        """Issue a replaceable direct upload action."""

        if self.fail_signing:
            raise ObjectStoreError('private provider signing detail')
        self.generation += 1
        path = self._path(relative_key)
        self.server.valid_generations[path] = self.generation
        headers = {'Content-Length': str(size)}
        if checksum_sha256 is not None:
            headers['x-amz-checksum-sha256'] = checksum_sha256
        return PresignedAction(method='PUT', url=f'{self._origin()}{path}?action={self.generation}', headers=headers, expires_in=expires_in)

    def presign_download(self, relative_key, *, expires_in):
        """Issue a direct download action for the stored test object."""

        if self.fail_signing:
            raise ObjectStoreError('private provider signing detail')
        return PresignedAction(method='GET', url=f'{self._origin()}{self._path(relative_key)}', expires_in=expires_in)

    def head(self, relative_key):
        """Return provider metadata derived from directly uploaded bytes."""

        if self.fail_head:
            raise ObjectStoreError('private provider metadata detail')
        content = self.server.objects.get(self._path(relative_key))
        if content is None:
            raise ObjectNotFound('private provider missing detail')
        checksum = base64.b64encode(hashlib.sha256(content).digest()).decode('ascii')
        return StoredObject(relative_key=relative_key, size=len(content), checksum_sha256=checksum)

    def _origin(self):
        """Return the test provider's loopback HTTP origin."""

        host, port = self.server.server_address
        return f'http://{host}:{port}'

    def _path(self, relative_key):
        """Map a trusted storage key into the provider's HTTP namespace."""

        return f'/objects/{quote(relative_key, safe="/")}'


class LfsDataPlaneIntegrationTests(TestCase):
    """Exercise the authenticated Git LFS lifecycle across HTTP boundaries."""

    def setUp(self):
        """Create credentials, a dataset, and a separate byte-transfer service."""

        self.user = User.objects.create_user(username='researcher')
        self.dataset = Dataset.objects.create(namespace=self.user.personal_namespace, slug='images', name='Images', created_by=self.user)
        _, self.read_token = create_access_token(user=self.user, name='Reader', scopes=['read_repository'], origin=AccessToken.Origin.CLI)
        _, self.write_token = create_access_token(user=self.user, name='Writer', scopes=['write_repository'], origin=AccessToken.Origin.CLI)
        self.client = Client()
        self.batch_url = f'/git/{self.dataset.id}.git/info/lfs/objects/batch'
        self.provider = ThreadingHTTPServer(('127.0.0.1', 0), DirectObjectHandler)
        self.provider.objects = {}
        self.provider.valid_generations = {}
        self.provider.put_paths = []
        self.provider.get_paths = []
        self.provider_thread = Thread(target=self.provider.serve_forever, daemon=True)
        self.provider_thread.start()
        self.object_store = NetworkedMemoryStore(self.provider)
        self.store_patcher = patch('datasets.lfs_transfers.S3ObjectStore', return_value=self.object_store)
        self.store_patcher.start()

    def tearDown(self):
        """Stop the isolated provider and restore the production adapter."""

        self.store_patcher.stop()
        self.provider.shutdown()
        self.provider.server_close()
        self.provider_thread.join(timeout=5)

    def post_batch(self, operation, objects, *, token=None):
        """Send the media type and payload used by a standard Git LFS client."""

        credentials = base64.b64encode(f'researcher:{token or self.write_token}'.encode()).decode()
        return self.client.post(
            self.batch_url,
            data={'operation': operation, 'transfers': ['basic'], 'hash_algo': 'sha256', 'objects': objects},
            content_type='application/vnd.git-lfs+json',
            HTTP_ACCEPT='application/vnd.git-lfs+json',
            HTTP_AUTHORIZATION=f'Basic {credentials}',
        )

    def test_basic_upload_verify_and_download_keep_bulk_bytes_out_of_django(self):
        """Transfer a complete object through signed provider actions only."""

        content = b'Niyan integration object\n'
        oid = hashlib.sha256(content).hexdigest()
        requested = {'oid': oid, 'size': len(content)}

        upload_response = self.post_batch('upload', [requested])
        upload_action = upload_response.json()['objects'][0]['actions']['upload']
        verify_action = upload_response.json()['objects'][0]['actions']['verify']
        self.assertEqual(upload_response.status_code, 200)
        self.assertNotEqual(urlsplit(upload_action['href']).netloc, 'testserver')

        request = Request(upload_action['href'], data=content, method='PUT', headers=upload_action['header'])
        with urlopen(request, timeout=5) as provider_response:
            self.assertEqual(provider_response.status, 200)

        verify_response = self.client.post(
            f'/git/{self.dataset.id}.git/info/lfs/objects/{oid}/verify',
            data=requested,
            content_type='application/vnd.git-lfs+json',
            HTTP_AUTHORIZATION=verify_action['header']['Authorization'],
        )
        download_response = self.post_batch('download', [requested], token=self.read_token)
        download_action = download_response.json()['objects'][0]['actions']['download']
        with urlopen(download_action['href'], timeout=5) as provider_response:
            downloaded = provider_response.read()

        lfs_object = LfsObject.objects.get(dataset=self.dataset, oid=oid)
        self.assertEqual(verify_response.status_code, 200)
        self.assertEqual(downloaded, content)
        self.assertEqual(lfs_object.state, LfsObject.State.AVAILABLE)
        self.assertEqual(lfs_object.verification_method, LfsObject.VerificationMethod.SHA256)
        self.assertEqual(len(self.provider.put_paths), 1)
        self.assertEqual(len(self.provider.get_paths), 1)

    def test_reacquiring_an_action_reuses_metadata_and_replaces_stale_authority(self):
        """Let clients safely retry negotiation after a signed action expires."""

        content = b'retryable object'
        requested = {'oid': hashlib.sha256(content).hexdigest(), 'size': len(content)}

        first_action = self.post_batch('upload', [requested]).json()['objects'][0]['actions']['upload']
        second_action = self.post_batch('upload', [requested]).json()['objects'][0]['actions']['upload']

        self.assertNotEqual(first_action['href'], second_action['href'])
        self.assertEqual(LfsObject.objects.filter(dataset=self.dataset, oid=requested['oid']).count(), 1)
        with self.assertRaises(HTTPError) as stale_error:
            urlopen(Request(first_action['href'], data=content, method='PUT', headers=first_action['header']), timeout=5)
        self.assertEqual(stale_error.exception.code, 403)
        with urlopen(Request(second_action['href'], data=content, method='PUT', headers=second_action['header']), timeout=5) as provider_response:
            self.assertEqual(provider_response.status, 200)

    def test_corrupt_content_and_provider_failures_remain_pending_and_sanitized(self):
        """Reject bad integrity evidence and hide private provider diagnostics."""

        expected = b'expected'
        corrupt = b'corrupt!'
        requested = {'oid': hashlib.sha256(expected).hexdigest(), 'size': len(expected)}
        action = self.post_batch('upload', [requested]).json()['objects'][0]['actions']['upload']
        with urlopen(Request(action['href'], data=corrupt, method='PUT', headers=action['header']), timeout=5):
            pass

        verify_url = f'/git/{self.dataset.id}.git/info/lfs/objects/{requested["oid"]}/verify'
        corrupt_response = self.client.post(verify_url, data=requested, content_type='application/vnd.git-lfs+json', HTTP_AUTHORIZATION=self._basic(self.write_token))
        self.object_store.fail_head = True
        unavailable_response = self.client.post(verify_url, data=requested, content_type='application/vnd.git-lfs+json', HTTP_AUTHORIZATION=self._basic(self.write_token))
        self.object_store.fail_signing = True
        signing_response = self.post_batch('upload', [{'oid': 'a' * 64, 'size': 4}])

        self.assertEqual(corrupt_response.status_code, 422)
        self.assertEqual(unavailable_response.status_code, 503)
        self.assertEqual(signing_response.json()['objects'][0]['error']['code'], 503)
        combined_body = corrupt_response.content + unavailable_response.content + signing_response.content
        self.assertNotIn(b'private provider', combined_body)
        self.assertEqual(LfsObject.objects.get(dataset=self.dataset, oid=requested['oid']).state, LfsObject.State.PENDING)

    def test_expired_bounded_and_revoked_authority_is_checked_on_each_request(self):
        """Intersect live credentials, resource boundaries, and current roles."""

        other_dataset = Dataset.objects.create(namespace=self.user.personal_namespace, slug='other', name='Other', created_by=self.user)
        _, bounded_token = create_access_token(user=self.user, name='Dataset token', scopes=['write_repository'], origin=AccessToken.Origin.CLI, dataset=self.dataset)
        _, wrong_boundary_token = create_access_token(user=self.user, name='Other token', scopes=['write_repository'], origin=AccessToken.Origin.CLI, dataset=other_dataset)
        expired_record, expired_token = create_access_token(user=self.user, name='Expired', scopes=['write_repository'], origin=AccessToken.Origin.CLI)
        AccessToken.objects.filter(pk=expired_record.pk).update(expires_at=timezone.now())

        allowed = self.post_batch('upload', [], token=bounded_token)
        wrong_boundary = self.post_batch('upload', [], token=wrong_boundary_token)
        expired = self.post_batch('upload', [], token=expired_token)

        collaborator = User.objects.create_user(username='collaborator')
        grant = DatasetGrant.objects.create(dataset=self.dataset, user=collaborator, role=NamespaceMembership.Role.CONTRIBUTOR)
        _, collaborator_token = create_access_token(user=collaborator, name='Collaborator', scopes=['write_repository'], origin=AccessToken.Origin.CLI)
        content = b'authorization changes'
        requested = {'oid': hashlib.sha256(content).hexdigest(), 'size': len(content)}
        negotiated = self.post_batch('upload', [requested], token=collaborator_token)
        verify_authorization = negotiated.json()['objects'][0]['actions']['verify']['header']['Authorization']
        grant.delete()
        denied_verify = self.client.post(
            f'/git/{self.dataset.id}.git/info/lfs/objects/{requested["oid"]}/verify',
            data=requested,
            content_type='application/vnd.git-lfs+json',
            HTTP_AUTHORIZATION=verify_authorization,
        )

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(wrong_boundary.status_code, 403)
        self.assertEqual(expired.status_code, 401)
        self.assertEqual(negotiated.status_code, 200)
        self.assertEqual(denied_verify.status_code, 404)

    def _basic(self, token, *, username='researcher'):
        """Encode one Git HTTPS Basic credential header."""

        encoded = base64.b64encode(f'{username}:{token}'.encode()).decode()
        return f'Basic {encoded}'
