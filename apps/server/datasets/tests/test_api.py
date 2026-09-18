from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from django.test import Client, TestCase, override_settings
from django.utils.dateparse import parse_datetime

from accounts.models import AccessToken, User
from accounts.tokens import create_access_token
from datasets.models import Dataset
from datasets.object_storage import ObjectStoreError
from datasets.repositories import RepositoryDeletionError


class DatasetApiTests(TestCase):
    """Verify the authenticated dataset-management HTTP contract."""

    def setUp(self):
        """Create a user, authenticated client, and temporary repository root."""

        self.user = User.objects.create_user(username='researcher')
        self.client = Client()
        self.client.force_login(self.user)
        self.repository_directory = TemporaryDirectory()
        self.repository_root = Path(self.repository_directory.name)
        self.settings_override = override_settings(REPOSITORIES_ROOT=self.repository_root)
        self.settings_override.enable()
        self.object_store_patcher = patch('datasets.services.S3ObjectStore')
        self.object_store_class = self.object_store_patcher.start()
        self.object_store = self.object_store_class.return_value

    def tearDown(self):
        """Restore settings before removing the temporary repository root."""

        self.object_store_patcher.stop()
        self.settings_override.disable()
        self.repository_directory.cleanup()

    def post_dataset(self, **overrides):
        """Submit a dataset-creation request with optional field overrides.

        Parameters
        ----------
        **overrides
            Request fields that replace the valid defaults.

        Returns
        -------
        django.http.HttpResponse
            Response returned by the versioned API.
        """

        payload = {
            'namespace_id': str(self.user.personal_namespace.id),
            'slug': 'Images',
            'name': 'Research Images',
            'description': 'Training and validation images.',
        }
        payload.update(overrides)
        return self.client.post('/api/v1/datasets', payload, content_type='application/json')

    def test_create_dataset_returns_public_identity_and_provisions_repository(self):
        """Create a dataset through the API without exposing its physical path."""

        response = self.post_dataset()

        self.assertEqual(response.status_code, 201)
        body = response.json()
        dataset = Dataset.objects.get()
        self.assertEqual(
            {key: value for key, value in body.items() if key != 'created_at'},
            {
                'id': str(dataset.id),
                'namespace_id': str(self.user.personal_namespace.id),
                'namespace_path': 'researcher',
                'slug': 'images',
                'name': 'Research Images',
                'description': 'Training and validation images.',
                'default_branch': 'main',
                'role': 'owner',
            },
        )
        self.assertLess(abs(parse_datetime(body['created_at']) - dataset.created_at), timedelta(milliseconds=1))
        self.assertTrue((self.repository_root / f'{dataset.id}.git').is_dir())
        self.assertNotIn('repository_path', body)

    def test_create_dataset_requires_authentication(self):
        """Reject anonymous creation before invoking the domain service."""

        self.client.logout()

        response = self.post_dataset()

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {'code': 'authentication_required', 'detail': 'Authentication is required.'})
        self.assertFalse(Dataset.objects.exists())

    def test_create_dataset_rejects_foreign_personal_namespace(self):
        """Translate domain authorization failure into a forbidden response."""

        another_user = User.objects.create_user(username='another-researcher')

        response = self.post_dataset(namespace_id=str(another_user.personal_namespace.id))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'permission_denied')
        self.assertFalse(Dataset.objects.exists())

    def test_create_dataset_reports_unknown_namespace(self):
        """Return not found for an immutable namespace identity that is absent."""

        response = self.post_dataset(namespace_id=str(uuid4()))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'namespace_not_found')

    def test_create_dataset_reports_duplicate_path(self):
        """Return a conflict when the namespace path is already reserved."""

        self.assertEqual(self.post_dataset().status_code, 201)

        response = self.post_dataset(name='Other Images')

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'dataset_path_conflict')
        self.assertEqual(Dataset.objects.count(), 1)

    def test_create_dataset_reports_invalid_request_data(self):
        """Return the stable validation error shape for malformed input."""

        response = self.post_dataset(namespace_id='not-a-uuid')

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {'code': 'validation_error', 'detail': 'The request data is invalid.'})
        self.assertFalse(Dataset.objects.exists())

    def test_create_dataset_reports_invalid_dataset_slug(self):
        """Translate domain validation without returning validator internals."""

        response = self.post_dataset(slug='not/a/path')

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {'code': 'validation_error', 'detail': 'The dataset details are invalid.'})
        self.assertFalse(Dataset.objects.exists())

    def test_create_dataset_hides_repository_provisioning_details(self):
        """Return a sanitized service failure when repository storage is unavailable."""

        missing_root = self.repository_root / 'missing'
        with override_settings(REPOSITORIES_ROOT=missing_root):
            response = self.post_dataset()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'code': 'repository_unavailable', 'detail': 'The dataset repository could not be created.'})
        self.assertNotIn(str(missing_root), response.content.decode())
        self.assertFalse(Dataset.objects.exists())

    def test_list_datasets_returns_a_deterministic_paginated_page(self):
        """List only the requested page with explicit pagination metadata."""

        first_id = self.post_dataset(slug='first', name='First').json()['id']
        second_id = self.post_dataset(slug='second', name='Second').json()['id']

        response = self.client.get('/api/v1/datasets', {'namespace_id': str(self.user.personal_namespace.id), 'limit': 1, 'offset': 1})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['count'], 2)
        self.assertEqual(body['limit'], 1)
        self.assertEqual(body['offset'], 1)
        self.assertEqual([item['id'] for item in body['items']], [second_id])
        self.assertNotEqual(first_id, second_id)

    def test_list_datasets_without_namespace_returns_every_visible_dataset(self):
        """Support the CLI's installation-wide visible dataset listing."""

        first_id = self.post_dataset(slug='first', name='First').json()['id']
        second_id = self.post_dataset(slug='second', name='Second').json()['id']

        response = self.client.get('/api/v1/datasets')

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.json()['items']], [first_id, second_id])

    def test_dataset_bound_token_global_list_cannot_expose_other_datasets(self):
        """Apply a token's immutable dataset boundary before global listing."""

        allowed_id = self.post_dataset(slug='allowed', name='Allowed').json()['id']
        self.post_dataset(slug='other', name='Other')
        allowed_dataset = Dataset.objects.get(pk=allowed_id)
        _, raw_token = create_access_token(user=self.user, name='Bounded reader', scopes=['read_api'], origin=AccessToken.Origin.MANUAL, dataset=allowed_dataset)
        self.client.logout()

        response = self.client.get('/api/v1/datasets', HTTP_AUTHORIZATION=f'Bearer {raw_token}')

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.json()['items']], [allowed_id])

    def test_list_datasets_hides_another_users_namespace(self):
        """Treat an inaccessible namespace as absent during reads."""

        another_user = User.objects.create_user(username='another-researcher')

        response = self.client.get('/api/v1/datasets', {'namespace_id': str(another_user.personal_namespace.id)})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'namespace_not_found')

    def test_list_datasets_rejects_page_larger_than_public_limit(self):
        """Reject pagination that exceeds the documented maximum page size."""

        response = self.client.get('/api/v1/datasets', {'namespace_id': str(self.user.personal_namespace.id), 'limit': 101})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {'code': 'validation_error', 'detail': 'The request data is invalid.'})

    def test_get_dataset_returns_visible_dataset(self):
        """Retrieve an owned dataset by its immutable identity."""

        created = self.post_dataset().json()

        response = self.client.get(f"/api/v1/datasets/{created['id']}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), created)

    def test_get_dataset_hides_another_users_dataset(self):
        """Use the not-found response for a private inaccessible dataset."""

        another_user = User.objects.create_user(username='another-researcher')
        self.client.force_login(another_user)
        dataset_id = self.post_dataset(namespace_id=str(another_user.personal_namespace.id)).json()['id']
        self.client.force_login(self.user)

        response = self.client.get(f'/api/v1/datasets/{dataset_id}')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'dataset_not_found')

    def test_update_dataset_changes_name_and_slug_without_moving_repository(self):
        """Update mutable metadata while preserving UUID-derived repository identity."""

        created = self.post_dataset().json()
        repository_path = self.repository_root / f"{created['id']}.git"

        response = self.client.patch(f"/api/v1/datasets/{created['id']}", {'slug': 'Microscopy', 'name': 'Microscopy Images', 'description': 'Confocal microscopy captures.'}, content_type='application/json')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['id'], created['id'])
        self.assertEqual(body['namespace_path'], 'researcher')
        self.assertEqual(body['slug'], 'microscopy')
        self.assertEqual(body['name'], 'Microscopy Images')
        self.assertEqual(body['description'], 'Confocal microscopy captures.')
        self.assertTrue(repository_path.is_dir())
        dataset = Dataset.objects.get()
        self.assertEqual(dataset.path, 'researcher/microscopy')

    def test_update_dataset_can_clear_description(self):
        """Treat the optional description as mutable presentation metadata."""

        created = self.post_dataset().json()

        response = self.client.patch(f"/api/v1/datasets/{created['id']}", {'description': ''}, content_type='application/json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['description'], '')
        self.assertEqual(Dataset.objects.get(pk=created['id']).description, '')

    def test_update_dataset_reports_conflicting_path(self):
        """Reject a rename that would collide with another dataset."""

        first_id = self.post_dataset(slug='first', name='First').json()['id']
        self.post_dataset(slug='second', name='Second')

        response = self.client.patch(f'/api/v1/datasets/{first_id}', {'slug': 'second'}, content_type='application/json')

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'dataset_path_conflict')
        self.assertEqual(Dataset.objects.get(pk=first_id).slug, 'first')

    def test_update_dataset_rejects_empty_patch(self):
        """Reject a PATCH request that contains no field changes."""

        dataset_id = self.post_dataset().json()['id']

        response = self.client.patch(f'/api/v1/datasets/{dataset_id}', {}, content_type='application/json')

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {'code': 'validation_error', 'detail': 'The request data is invalid.'})

    def test_update_dataset_rejects_explicit_null(self):
        """Keep non-null model fields non-null through partial updates."""

        dataset_id = self.post_dataset().json()['id']

        response = self.client.patch(f'/api/v1/datasets/{dataset_id}', {'name': None}, content_type='application/json')

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {'code': 'validation_error', 'detail': 'The request data is invalid.'})

    def test_delete_dataset_permanently_removes_record_and_repository(self):
        """Remove both control-plane identity and UUID-addressed Git storage."""

        dataset_id = self.post_dataset().json()['id']
        repository_path = self.repository_root / f'{dataset_id}.git'

        response = self.client.delete(f'/api/v1/datasets/{dataset_id}')

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b'')
        self.assertFalse(Dataset.objects.filter(pk=dataset_id).exists())
        self.assertFalse(repository_path.exists())
        self.assertEqual(self.client.get(f'/api/v1/datasets/{dataset_id}').status_code, 404)
        self.object_store.delete_prefix.assert_called_once_with(f'datasets/{dataset_id}')

    def test_delete_dataset_hides_another_users_dataset(self):
        """Prevent permanent deletion without revealing private dataset existence."""

        another_user = User.objects.create_user(username='another-researcher')
        self.client.force_login(another_user)
        dataset_id = self.post_dataset(namespace_id=str(another_user.personal_namespace.id)).json()['id']
        repository_path = self.repository_root / f'{dataset_id}.git'
        self.client.force_login(self.user)

        response = self.client.delete(f'/api/v1/datasets/{dataset_id}')

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Dataset.objects.filter(pk=dataset_id).exists())
        self.assertTrue(repository_path.is_dir())

    def test_delete_dataset_can_resume_after_repository_failure(self):
        """Hide a failed deletion immediately and complete it safely on retry."""

        dataset_id = self.post_dataset().json()['id']

        with patch('datasets.services.GitRepositoryStore.delete', side_effect=RepositoryDeletionError('private filesystem detail')):
            failed_response = self.client.delete(f'/api/v1/datasets/{dataset_id}')

        self.assertEqual(failed_response.status_code, 503)
        self.assertNotIn('private filesystem detail', failed_response.content.decode())
        self.assertEqual(self.client.get(f'/api/v1/datasets/{dataset_id}').status_code, 404)
        self.assertIsNotNone(Dataset.objects.get(pk=dataset_id).deletion_started_at)

        retry_response = self.client.delete(f'/api/v1/datasets/{dataset_id}')

        self.assertEqual(retry_response.status_code, 204)
        self.assertFalse(Dataset.objects.filter(pk=dataset_id).exists())

    def test_delete_dataset_keeps_record_and_repository_after_storage_failure(self):
        """Retry irreversible deletion instead of orphaning undeleted S3 content."""

        dataset_id = self.post_dataset().json()['id']
        repository_path = self.repository_root / f'{dataset_id}.git'
        self.object_store.delete_prefix.side_effect = ObjectStoreError('private provider detail')

        response = self.client.delete(f'/api/v1/datasets/{dataset_id}')

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['code'], 'object_storage_unavailable')
        self.assertNotIn('private provider detail', response.content.decode())
        self.assertTrue(repository_path.exists())
        self.assertIsNotNone(Dataset.objects.get(pk=dataset_id).deletion_started_at)

    def test_read_endpoints_require_authentication(self):
        """Protect list and detail reads with the router's session authentication."""

        dataset_id = self.post_dataset().json()['id']
        self.client.logout()

        list_response = self.client.get('/api/v1/datasets', {'namespace_id': str(self.user.personal_namespace.id)})
        detail_response = self.client.get(f'/api/v1/datasets/{dataset_id}')

        self.assertEqual(list_response.status_code, 401)
        self.assertEqual(detail_response.status_code, 401)

    def test_delete_endpoint_requires_authentication(self):
        """Protect permanent deletion with session authentication."""

        dataset_id = self.post_dataset().json()['id']
        self.client.logout()

        response = self.client.delete(f'/api/v1/datasets/{dataset_id}')

        self.assertEqual(response.status_code, 401)
        self.assertTrue(Dataset.objects.filter(pk=dataset_id).exists())
