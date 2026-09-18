import base64
from datetime import timedelta
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import AccessToken, User
from accounts.tokens import create_access_token
from datasets.lfs_transfers import LfsIntegrityError, LfsMultipartExpired, abort_multipart_upload, complete_multipart_upload, initiate_multipart_upload, issue_multipart_part_action
from datasets.models import Dataset, LfsMultipartUpload, LfsObject
from datasets.object_storage import CompletedPart, ObjectStoreError, PresignedAction, StoredObject


MIB = 1024 * 1024


class RecordingMultipartStore:
    """Implement deterministic multipart control without transferring bytes."""

    supports_sha256_checksums = False

    def __init__(self):
        """Initialize provider call records and upload identifier sequence."""

        self.calls = []
        self.upload_number = 0
        self.fail_completion = False

    def initiate_multipart(self, relative_key, *, checksum_algorithm=None):
        """Return one deterministic provider upload identifier."""

        self.upload_number += 1
        upload_id = f'provider-upload-{self.upload_number}'
        self.calls.append(('initiate', relative_key, upload_id, checksum_algorithm))
        return upload_id

    def presign_upload_part(self, relative_key, *, upload_id, part_number, size, expires_in, checksum_sha256=None):
        """Return one deterministic exact-size part action."""

        self.calls.append(('part', relative_key, upload_id, part_number, size, expires_in, checksum_sha256))
        return PresignedAction(method='PUT', url=f'https://storage.example.test/part/{part_number}', headers={'Content-Length': str(size)}, expires_in=expires_in)

    def complete_multipart(self, relative_key, *, upload_id, parts):
        """Record ordered completion metadata and return assembled metadata."""

        self.calls.append(('complete', relative_key, upload_id, [part.part_number for part in parts]))
        if self.fail_completion:
            raise ObjectStoreError('provider completion response was lost')
        return StoredObject(relative_key=relative_key, size=11 * MIB)

    def head(self, relative_key):
        """Return exact-size assembled object metadata."""

        self.calls.append(('head', relative_key))
        return StoredObject(relative_key=relative_key, size=11 * MIB)

    def abort_multipart(self, relative_key, *, upload_id):
        """Record one provider abort request."""

        self.calls.append(('abort', relative_key, upload_id))


@override_settings(NIYAN_LFS_MULTIPART_THRESHOLD_BYTES=10 * MIB, NIYAN_LFS_MULTIPART_PART_SIZE_BYTES=5 * MIB, NIYAN_LFS_MULTIPART_SESSION_LIFETIME_SECONDS=60, NIYAN_LFS_TRANSFER_ACTION_LIFETIME_SECONDS=300)
class LfsMultipartServiceTests(TestCase):
    """Verify opaque multipart sessions and exact provider control metadata."""

    def setUp(self):
        """Create one pending large object and deterministic object store."""

        user = User.objects.create_user(username='researcher')
        dataset = Dataset.objects.create(namespace=user.personal_namespace, slug='images', name='Images', created_by=user)
        self.lfs_object = LfsObject.objects.create(dataset=dataset, oid='a' * 64, size=11 * MIB)
        self.store = RecordingMultipartStore()
        self.now = timezone.now()

    def test_initiation_is_idempotent_and_keeps_provider_id_private(self):
        """Reuse one active session without creating another provider upload."""

        first = initiate_multipart_upload(lfs_object=self.lfs_object, object_store=self.store, now=self.now)
        second = initiate_multipart_upload(lfs_object=self.lfs_object, object_store=self.store, now=self.now + timedelta(seconds=1))

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.part_size, 5 * MIB)
        self.assertEqual(first.expected_part_count, 3)
        self.assertEqual([first.expected_part_size(number) for number in (1, 2, 3)], [5 * MIB, 5 * MIB, MIB])
        self.assertEqual([call[0] for call in self.store.calls], ['initiate'])
        self.assertNotEqual(str(first.id), first.provider_upload_id)

    def test_expired_session_is_aborted_before_replacement(self):
        """Make a replacement provider upload only after retiring expired state."""

        expired = initiate_multipart_upload(lfs_object=self.lfs_object, object_store=self.store, now=self.now)
        replacement = initiate_multipart_upload(lfs_object=self.lfs_object, object_store=self.store, now=self.now + timedelta(seconds=61))
        expired.refresh_from_db()

        self.assertEqual(expired.state, LfsMultipartUpload.State.ABORTED)
        self.assertNotEqual(expired.id, replacement.id)
        self.assertEqual([call[0] for call in self.store.calls], ['initiate', 'abort', 'initiate'])

    def test_part_action_requires_exact_server_selected_size(self):
        """Bind each signed URL to the immutable session layout."""

        session = initiate_multipart_upload(lfs_object=self.lfs_object, object_store=self.store, now=self.now)

        action = issue_multipart_part_action(session=session, part_number=3, size=MIB, object_store=self.store, now=self.now)

        self.assertEqual(action.headers, {'Content-Length': str(MIB)})
        with self.assertRaises(LfsIntegrityError):
            issue_multipart_part_action(session=session, part_number=3, size=MIB + 1, object_store=self.store, now=self.now)
        with self.assertRaises(ValueError):
            issue_multipart_part_action(session=session, part_number=4, size=1, object_store=self.store, now=self.now)

    def test_expired_session_rejects_new_part_actions(self):
        """Require renegotiation instead of extending an expired upload implicitly."""

        session = initiate_multipart_upload(lfs_object=self.lfs_object, object_store=self.store, now=self.now)

        with self.assertRaises(LfsMultipartExpired):
            issue_multipart_part_action(session=session, part_number=1, size=5 * MIB, object_store=self.store, now=self.now + timedelta(seconds=61))

    def test_completion_finalizes_object_and_is_retry_safe(self):
        """Publish only a complete ordered part set and reuse terminal state."""

        session = initiate_multipart_upload(lfs_object=self.lfs_object, object_store=self.store, now=self.now)
        parts = [CompletedPart(part_number=number, etag=f'"etag-{number}"') for number in (1, 2, 3)]

        completed = complete_multipart_upload(session=session, parts=parts, object_store=self.store, now=self.now)
        calls_after_completion = list(self.store.calls)
        retried = complete_multipart_upload(session=completed, parts=parts, object_store=self.store, now=self.now)
        self.lfs_object.refresh_from_db()

        self.assertEqual(completed.state, LfsMultipartUpload.State.COMPLETED)
        self.assertEqual(retried.state, LfsMultipartUpload.State.COMPLETED)
        self.assertEqual(self.lfs_object.state, LfsObject.State.AVAILABLE)
        self.assertEqual(self.lfs_object.verification_method, LfsObject.VerificationMethod.SIZE)
        self.assertEqual(self.store.calls, calls_after_completion)

    def test_completion_rejects_missing_or_reordered_parts(self):
        """Prevent provider assembly from an incomplete declared layout."""

        session = initiate_multipart_upload(lfs_object=self.lfs_object, object_store=self.store, now=self.now)

        with self.assertRaises(LfsIntegrityError):
            complete_multipart_upload(
                session=session,
                parts=[CompletedPart(part_number=1, etag='"one"'), CompletedPart(part_number=3, etag='"three"')],
                object_store=self.store,
                now=self.now,
            )

    def test_completion_recovers_when_provider_response_was_lost(self):
        """Finalize an already assembled canonical object after an uncertain response."""

        session = initiate_multipart_upload(lfs_object=self.lfs_object, object_store=self.store, now=self.now)
        parts = [CompletedPart(part_number=number, etag=f'"etag-{number}"') for number in (1, 2, 3)]
        self.store.fail_completion = True

        completed = complete_multipart_upload(session=session, parts=parts, object_store=self.store, now=self.now)
        self.lfs_object.refresh_from_db()

        self.assertEqual(completed.state, LfsMultipartUpload.State.COMPLETED)
        self.assertEqual(self.lfs_object.state, LfsObject.State.AVAILABLE)
        self.assertIn('head', [call[0] for call in self.store.calls])

    def test_abort_is_idempotent(self):
        """Avoid repeating provider mutation after terminal abort state persists."""

        session = initiate_multipart_upload(lfs_object=self.lfs_object, object_store=self.store, now=self.now)
        aborted = abort_multipart_upload(session=session, object_store=self.store, now=self.now)
        calls_after_abort = list(self.store.calls)
        retried = abort_multipart_upload(session=aborted, object_store=self.store, now=self.now)

        self.assertEqual(retried.state, LfsMultipartUpload.State.ABORTED)
        self.assertEqual(self.store.calls, calls_after_abort)


class LfsMultipartApiTests(TestCase):
    """Verify Bearer authorization and public multipart response boundaries."""

    def setUp(self):
        """Create one dataset, object, session, and scoped credentials."""

        self.user = User.objects.create_user(username='researcher')
        self.dataset = Dataset.objects.create(namespace=self.user.personal_namespace, slug='images', name='Images', created_by=self.user)
        self.lfs_object = LfsObject.objects.create(dataset=self.dataset, oid='b' * 64, size=11 * MIB)
        self.session = LfsMultipartUpload.objects.create(
            lfs_object=self.lfs_object,
            provider_upload_id='private-provider-upload-id',
            part_size=5 * MIB,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        _, self.write_token = create_access_token(user=self.user, name='Writer', scopes=['write_repository'], origin=AccessToken.Origin.CLI)
        _, self.read_token = create_access_token(user=self.user, name='Reader', scopes=['read_repository'], origin=AccessToken.Origin.CLI)
        self.client = Client()
        self.root = f'/api/v1/datasets/{self.dataset.id}/lfs/objects/{self.lfs_object.oid}/multipart'

    def bearer(self, token):
        """Return one Bearer authorization header dictionary."""

        return {'HTTP_AUTHORIZATION': f'Bearer {token}'}

    def test_initiation_requires_write_token_and_hides_provider_id(self):
        """Use the existing access-token model without exposing S3 session state."""

        with patch('datasets.lfs_api.initiate_multipart_upload', return_value=self.session):
            response = self.client.post(self.root, data={'size': self.lfs_object.size}, content_type='application/json', **self.bearer(self.write_token))
        forbidden = self.client.post(self.root, data={'size': self.lfs_object.size}, content_type='application/json', **self.bearer(self.read_token))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['session_id'], str(self.session.id))
        self.assertEqual(response.json()['part_count'], 3)
        self.assertNotIn('provider', response.content.decode())
        self.assertEqual(forbidden.status_code, 403)

    def test_part_completion_and_abort_endpoints_return_only_public_state(self):
        """Keep provider identifiers out of every multipart-control response."""

        part_action = PresignedAction(method='PUT', url='https://storage.example.test/part', headers={'Content-Length': str(5 * MIB)}, expires_in=300)
        part_url = f'{self.root}/{self.session.id}/parts/1'
        complete_url = f'{self.root}/{self.session.id}/complete'
        abort_url = f'{self.root}/{self.session.id}'
        completed = self.session
        completed.state = LfsMultipartUpload.State.COMPLETED
        aborted = LfsMultipartUpload.objects.get(pk=self.session.pk)
        aborted.state = LfsMultipartUpload.State.ABORTED

        with patch('datasets.lfs_api.issue_multipart_part_action', return_value=part_action):
            part_response = self.client.post(part_url, data={'size': 5 * MIB}, content_type='application/json', **self.bearer(self.write_token))
        with patch('datasets.lfs_api.complete_multipart_upload', return_value=completed):
            complete_response = self.client.post(complete_url, data={'parts': [{'part_number': 1, 'etag': '"etag"'}]}, content_type='application/json', **self.bearer(self.write_token))
        with patch('datasets.lfs_api.abort_multipart_upload', return_value=aborted):
            abort_response = self.client.delete(abort_url, **self.bearer(self.write_token))

        self.assertEqual(part_response.json()['href'], part_action.url)
        self.assertEqual(complete_response.json()['state'], 'completed')
        self.assertEqual(abort_response.json()['state'], 'aborted')
        self.assertNotIn('private-provider-upload-id', part_response.content.decode() + complete_response.content.decode() + abort_response.content.decode())

    def test_control_endpoints_enforce_dataset_boundary_and_current_role(self):
        """Reauthorize the same access token and dataset policy on every control call."""

        other_dataset = Dataset.objects.create(namespace=self.user.personal_namespace, slug='other', name='Other', created_by=self.user)
        _, bounded_token = create_access_token(user=self.user, name='Other only', scopes=['write_repository'], origin=AccessToken.Origin.CLI, dataset=other_dataset)
        outsider = User.objects.create_user(username='outsider')
        _, outsider_token = create_access_token(user=outsider, name='Writer', scopes=['write_repository'], origin=AccessToken.Origin.CLI)

        wrong_boundary = self.client.post(self.root, data={'size': self.lfs_object.size}, content_type='application/json', **self.bearer(bounded_token))
        hidden = self.client.post(self.root, data={'size': self.lfs_object.size}, content_type='application/json', **self.bearer(outsider_token))

        self.assertEqual(wrong_boundary.status_code, 403)
        self.assertEqual(hidden.status_code, 404)


class LfsMultipartBatchTests(TestCase):
    """Verify custom transfer selection while retaining standard small PUTs."""

    def setUp(self):
        """Create one authorized dataset and Basic credential."""

        self.user = User.objects.create_user(username='researcher')
        self.dataset = Dataset.objects.create(namespace=self.user.personal_namespace, slug='images', name='Images', created_by=self.user)
        _, self.token = create_access_token(user=self.user, name='Writer', scopes=['write_repository'], origin=AccessToken.Origin.CLI)
        encoded = base64.b64encode(f'researcher:{self.token}'.encode()).decode()
        self.authorization = f'Basic {encoded}'
        self.client = Client()
        self.url = f'/git/{self.dataset.id}.git/info/lfs/objects/batch'

    @override_settings(NIYAN_LFS_MULTIPART_THRESHOLD_BYTES=12)
    def test_mixed_batch_selects_custom_agent_and_keeps_small_direct_put(self):
        """Route the large object to REST while the same agent uploads small content directly."""

        small_oid = 'c' * 64
        large_oid = 'd' * 64
        with patch(
            'datasets.lfs_http.issue_upload_action',
            return_value=PresignedAction(method='PUT', url='https://storage.example.test/small', headers={'Content-Length': '5'}, expires_in=300),
        ):
            response = self.client.post(
                self.url,
                data={
                    'operation': 'upload',
                    'transfers': ['basic', 'niyan-multipart'],
                    'objects': [{'oid': small_oid, 'size': 5}, {'oid': large_oid, 'size': 12}],
                },
                content_type='application/vnd.git-lfs+json',
                HTTP_AUTHORIZATION=self.authorization,
            )

        payload = response.json()
        self.assertEqual(payload['transfer'], 'niyan-multipart')
        self.assertEqual(payload['objects'][0]['actions']['upload']['href'], 'https://storage.example.test/small')
        self.assertEqual(payload['objects'][1]['actions']['upload']['href'], f'http://testserver/api/v1/datasets/{self.dataset.id}/lfs/objects/{large_oid}/multipart')
        self.assertNotIn('Authorization', payload['objects'][1]['actions']['upload'].get('header', {}))
