import base64
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import AccessToken, User
from accounts.tokens import create_access_token
from datasets.lfs_transfers import LfsIntegrityError, LfsObjectMissing
from datasets.models import Dataset, LfsObject
from datasets.object_storage import PresignedAction


class LfsBatchApiTests(TestCase):
    """Verify standard Git LFS Batch negotiation and authorization."""

    def setUp(self):
        """Create one dataset owner and repository-scoped credentials."""

        self.user = User.objects.create_user(username='researcher')
        self.dataset = Dataset.objects.create(namespace=self.user.personal_namespace, slug='images', name='Images', created_by=self.user)
        _, self.read_token = create_access_token(user=self.user, name='Reader', scopes=['read_repository'], origin=AccessToken.Origin.CLI)
        self.write_access_token, self.write_token = create_access_token(user=self.user, name='Writer', scopes=['write_repository'], origin=AccessToken.Origin.CLI)
        self.client = Client()
        self.url = f'/git/{self.dataset.id}.git/info/lfs/objects/batch'

    def post_batch(self, payload, *, token=None, content_type='application/vnd.git-lfs+json'):
        """Post one authenticated batch request with the Git LFS media type."""

        credentials = base64.b64encode(f'researcher:{token or self.write_token}'.encode()).decode()
        return self.client.post(self.url, data=payload, content_type=content_type, HTTP_AUTHORIZATION=f'Basic {credentials}')

    def create_available_object(self, *, oid='b' * 64, size=12):
        """Persist one size-verified object available for download."""

        return LfsObject.objects.create(
            dataset=self.dataset,
            oid=oid,
            size=size,
            state=LfsObject.State.AVAILABLE,
            verification_method=LfsObject.VerificationMethod.SIZE,
            available_at=timezone.now(),
        )

    @override_settings(NIYAN_LFS_MULTIPART_THRESHOLD_BYTES=100)
    def test_upload_creates_pending_metadata_and_returns_direct_action(self):
        """Negotiate a basic upload while keeping its bytes out of Django."""

        oid = 'a' * 64
        with self.settings(NIYAN_LFS_TRANSFER_ACTION_LIFETIME_SECONDS=300):
            with patch(
                'datasets.lfs_http.issue_upload_action',
                return_value=PresignedAction(method='PUT', url='https://storage.example.test/signed', headers={'Content-Length': '12'}, expires_in=300),
            ) as issue_action:
                response = self.post_batch({'operation': 'upload', 'transfers': ['basic'], 'hash_algo': 'sha256', 'objects': [{'oid': oid, 'size': 12}]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/vnd.git-lfs+json')
        response_body = response.json()
        verify_action = response_body['objects'][0]['actions'].pop('verify')
        self.assertEqual(
            response_body,
            {
                'transfer': 'basic',
                'hash_algo': 'sha256',
                'objects': [
                    {
                        'oid': oid,
                        'size': 12,
                        'authenticated': True,
                        'actions': {
                            'upload': {'href': 'https://storage.example.test/signed', 'expires_in': 300, 'header': {'Content-Length': '12'}},
                        },
                    }
                ],
            },
        )
        self.assertEqual(verify_action['href'], f'http://testserver/git/{self.dataset.id}.git/info/lfs/objects/{oid}/verify')
        self.assertEqual(verify_action['expires_in'], 300)
        self.assertTrue(verify_action['header']['Authorization'].startswith('Bearer '))
        self.assertNotIn(self.write_token, verify_action['header']['Authorization'])
        lfs_object = LfsObject.objects.get(dataset=self.dataset, oid=oid)
        self.assertEqual(lfs_object.state, LfsObject.State.PENDING)
        issue_action.assert_called_once_with(lfs_object=lfs_object)

    def test_upload_reuses_available_object_without_action(self):
        """Tell Git LFS existing verified content needs no upload."""

        lfs_object = self.create_available_object()

        response = self.post_batch({'operation': 'upload', 'objects': [{'oid': lfs_object.oid, 'size': lfs_object.size}]})

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('actions', response.json()['objects'][0])

    def test_download_returns_action_only_for_available_matching_object(self):
        """Return a GET action for available content and per-object failures otherwise."""

        available = self.create_available_object()
        pending = LfsObject.objects.create(dataset=self.dataset, oid='c' * 64, size=20)
        with patch(
            'datasets.lfs_http.issue_download_action',
            return_value=PresignedAction(method='GET', url='https://storage.example.test/download', expires_in=300),
        ):
            response = self.post_batch(
                {
                    'operation': 'download',
                    'objects': [
                        {'oid': available.oid, 'size': available.size},
                        {'oid': pending.oid, 'size': pending.size},
                        {'oid': 'd' * 64, 'size': 1},
                    ],
                },
                token=self.read_token,
            )

        objects = response.json()['objects']
        self.assertEqual(objects[0]['actions']['download']['href'], 'https://storage.example.test/download')
        self.assertEqual(objects[1]['error']['code'], 404)
        self.assertEqual(objects[2]['error']['code'], 404)

    def test_size_conflicts_are_per_object_integrity_errors(self):
        """Never reuse dataset content whose declared size changed."""

        lfs_object = self.create_available_object()

        upload = self.post_batch({'operation': 'upload', 'objects': [{'oid': lfs_object.oid, 'size': lfs_object.size + 1}]})
        download = self.post_batch({'operation': 'download', 'objects': [{'oid': lfs_object.oid, 'size': lfs_object.size + 1}]}, token=self.read_token)

        self.assertEqual(upload.json()['objects'][0]['error']['code'], 422)
        self.assertEqual(download.json()['objects'][0]['error']['code'], 422)

    @override_settings(NIYAN_LFS_MULTIPART_THRESHOLD_BYTES=12)
    def test_large_upload_requires_niyan_multipart_agent(self):
        """Refuse unreliable single-request upload at the multipart threshold."""

        response = self.post_batch({'operation': 'upload', 'objects': [{'oid': 'e' * 64, 'size': 12}]})

        self.assertEqual(response.json()['objects'][0]['error']['code'], 422)
        self.assertIn('multipart', response.json()['objects'][0]['error']['message'])

    def test_verify_endpoint_finalizes_matching_pending_object(self):
        """Use the negotiated scoped capability for the standard verify action."""

        lfs_object = LfsObject.objects.create(dataset=self.dataset, oid='f' * 64, size=12)
        negotiated = self.post_batch({'operation': 'upload', 'objects': [{'oid': lfs_object.oid, 'size': lfs_object.size}]})
        authorization = negotiated.json()['objects'][0]['actions']['verify']['header']['Authorization']
        lfs_object.state = LfsObject.State.AVAILABLE
        with patch('datasets.lfs_http.finalize_lfs_upload', return_value=lfs_object) as finalize:
            response = self.client.post(
                f'/git/{self.dataset.id}.git/info/lfs/objects/{lfs_object.oid}/verify',
                data={'oid': lfs_object.oid, 'size': lfs_object.size},
                content_type='application/vnd.git-lfs+json',
                HTTP_AUTHORIZATION=authorization,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'oid': lfs_object.oid, 'size': lfs_object.size})
        finalize.assert_called_once()

    def test_verify_capability_is_exact_and_rechecks_live_token_state(self):
        """Reject replay for another object and revoke authority immediately."""

        first = LfsObject.objects.create(dataset=self.dataset, oid='4' * 64, size=12)
        second = LfsObject.objects.create(dataset=self.dataset, oid='5' * 64, size=12)
        negotiated = self.post_batch({'operation': 'upload', 'objects': [{'oid': first.oid, 'size': first.size}]})
        authorization = negotiated.json()['objects'][0]['actions']['verify']['header']['Authorization']

        wrong_object = self.client.post(
            f'/git/{self.dataset.id}.git/info/lfs/objects/{second.oid}/verify',
            data={'oid': second.oid, 'size': second.size},
            content_type='application/vnd.git-lfs+json',
            HTTP_AUTHORIZATION=authorization,
        )
        AccessToken.objects.filter(pk=self.write_access_token.pk).update(revoked_at=timezone.now())
        revoked = self.client.post(
            f'/git/{self.dataset.id}.git/info/lfs/objects/{first.oid}/verify',
            data={'oid': first.oid, 'size': first.size},
            content_type='application/vnd.git-lfs+json',
            HTTP_AUTHORIZATION=authorization,
        )

        self.assertEqual(wrong_object.status_code, 401)
        self.assertEqual(revoked.status_code, 401)

    def test_verify_endpoint_maps_missing_and_integrity_failures(self):
        """Return stable protocol failures without exposing provider details."""

        lfs_object = LfsObject.objects.create(dataset=self.dataset, oid='1' * 64, size=12)
        authorization = f'Basic {base64.b64encode(f"researcher:{self.write_token}".encode()).decode()}'
        url = f'/git/{self.dataset.id}.git/info/lfs/objects/{lfs_object.oid}/verify'
        with patch('datasets.lfs_http.finalize_lfs_upload', side_effect=LfsObjectMissing('provider secret')):
            missing = self.client.post(url, data={'oid': lfs_object.oid, 'size': 12}, content_type='application/vnd.git-lfs+json', HTTP_AUTHORIZATION=authorization)
        with patch('datasets.lfs_http.finalize_lfs_upload', side_effect=LfsIntegrityError('The uploaded Git LFS object failed SHA-256 verification.')):
            corrupt = self.client.post(url, data={'oid': lfs_object.oid, 'size': 12}, content_type='application/vnd.git-lfs+json', HTTP_AUTHORIZATION=authorization)

        self.assertEqual(missing.status_code, 404)
        self.assertNotIn('provider secret', missing.content.decode())
        self.assertEqual(corrupt.status_code, 422)
        self.assertIn('SHA-256 verification', corrupt.json()['message'])

    def test_verify_endpoint_requires_write_scope_and_exact_identity(self):
        """Reject weaker credentials and mismatched verification metadata."""

        lfs_object = LfsObject.objects.create(dataset=self.dataset, oid='2' * 64, size=12)
        url = f'/git/{self.dataset.id}.git/info/lfs/objects/{lfs_object.oid}/verify'
        read_authorization = f'Basic {base64.b64encode(f"researcher:{self.read_token}".encode()).decode()}'
        write_authorization = f'Basic {base64.b64encode(f"researcher:{self.write_token}".encode()).decode()}'

        forbidden = self.client.post(url, data={'oid': lfs_object.oid, 'size': 12}, content_type='application/vnd.git-lfs+json', HTTP_AUTHORIZATION=read_authorization)
        wrong_oid = self.client.post(url, data={'oid': '3' * 64, 'size': 12}, content_type='application/vnd.git-lfs+json', HTTP_AUTHORIZATION=write_authorization)
        wrong_size = self.client.post(url, data={'oid': lfs_object.oid, 'size': 13}, content_type='application/vnd.git-lfs+json', HTTP_AUTHORIZATION=write_authorization)

        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(wrong_oid.status_code, 422)
        self.assertEqual(wrong_size.status_code, 422)

    def test_basic_authentication_scope_boundary_and_current_role_are_enforced(self):
        """Apply credential and current dataset policy before exposing actions."""

        unauthenticated = self.client.post(self.url, data={'operation': 'download', 'objects': []}, content_type='application/vnd.git-lfs+json')
        wrong_scope = self.post_batch({'operation': 'upload', 'objects': []}, token=self.read_token)
        other_dataset = Dataset.objects.create(namespace=self.user.personal_namespace, slug='other', name='Other', created_by=self.user)
        _, bounded_token = create_access_token(user=self.user, name='Other only', scopes=['write_repository'], origin=AccessToken.Origin.CLI, dataset=other_dataset)
        wrong_boundary = self.post_batch({'operation': 'upload', 'objects': []}, token=bounded_token)
        outsider = User.objects.create_user(username='outsider')
        _, outsider_token = create_access_token(user=outsider, name='Writer', scopes=['write_repository'], origin=AccessToken.Origin.CLI)
        hidden = self.post_batch({'operation': 'upload', 'objects': []}, token=outsider_token)

        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(unauthenticated['WWW-Authenticate'], 'Basic realm="Niyan Git LFS"')
        self.assertEqual(wrong_scope.status_code, 403)
        self.assertEqual(wrong_boundary.status_code, 403)
        self.assertEqual(hidden.status_code, 404)

    def test_rejects_malformed_or_unsupported_batch_metadata(self):
        """Reject invalid protocol metadata before creating object records."""

        cases = [
            ({'operation': 'delete', 'objects': []}, 'application/vnd.git-lfs+json'),
            ({'operation': 'upload', 'hash_algo': 'sha1', 'objects': []}, 'application/vnd.git-lfs+json'),
            ({'operation': 'upload', 'transfers': ['other'], 'objects': []}, 'application/vnd.git-lfs+json'),
            ({'operation': 'upload', 'objects': [{'oid': 'A' * 64, 'size': 1}]}, 'application/vnd.git-lfs+json'),
            ({'operation': 'upload', 'objects': [{'oid': 'a' * 64, 'size': -1}]}, 'application/vnd.git-lfs+json'),
            ({'operation': 'upload', 'objects': []}, 'application/json'),
        ]

        for payload, content_type in cases:
            with self.subTest(payload=payload, content_type=content_type):
                response = self.post_batch(payload, content_type=content_type)
                self.assertEqual(response.status_code, 422)

        self.assertFalse(LfsObject.objects.exists())

    def test_rejects_more_than_one_hundred_objects(self):
        """Bound authorization, database work, and action signing per request."""

        objects = [{'oid': f'{index:064x}', 'size': 1} for index in range(101)]

        response = self.post_batch({'operation': 'upload', 'objects': objects})

        self.assertEqual(response.status_code, 413)
        self.assertFalse(LfsObject.objects.exists())
