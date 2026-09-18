from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import AccessToken, DeviceAuthorization, User
from accounts.tokens import create_access_token
from datasets.models import Dataset


class AccessTokenApiTests(TestCase):
    """Verify dashboard token management and bearer authentication."""

    def setUp(self):
        """Create a browser user and isolated Git repository root."""

        self.user = User.objects.create_user(username='researcher')
        self.client = Client()
        self.client.force_login(self.user)
        self.repository_directory = TemporaryDirectory()
        self.settings_override = override_settings(REPOSITORIES_ROOT=Path(self.repository_directory.name))
        self.settings_override.enable()

    def tearDown(self):
        """Restore repository settings and delete temporary repositories."""

        self.settings_override.disable()
        self.repository_directory.cleanup()

    def create_dataset(self, *, slug='images', name='Images'):
        """Create one dataset through the authenticated public API."""

        response = self.client.post(
            '/api/v1/datasets',
            {'namespace_id': str(self.user.personal_namespace.id), 'slug': slug, 'name': name},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)
        return Dataset.objects.get(pk=response.json()['id'])

    def issue_token(self, **overrides):
        """Create a token through the browser-session endpoint."""

        payload = {'name': 'Workstation', 'scopes': ['read_api']}
        payload.update(overrides)
        return self.client.post('/api/v1/auth/tokens', payload, content_type='application/json')

    def test_session_user_can_create_and_list_one_unified_access_token(self):
        """Return the secret once while retaining only non-secret metadata."""

        create_response = self.issue_token()

        self.assertEqual(create_response.status_code, 201)
        created = create_response.json()
        self.assertTrue(created['token'].startswith('niyan_'))
        self.assertEqual(created['origin'], 'manual')
        self.assertEqual(created['resource_boundary'], 'user')
        access_token = AccessToken.objects.get()
        self.assertNotIn(created['token'], access_token.secret_digest)

        list_response = self.client.get('/api/v1/auth/tokens')

        self.assertEqual(list_response.status_code, 200)
        listed = list_response.json()
        self.assertEqual(listed['count'], 1)
        self.assertNotIn('token', listed['items'][0])
        self.assertEqual(listed['items'][0]['fingerprint'], access_token.fingerprint)

    def test_access_token_cannot_create_another_access_token(self):
        """Require a browser session for credential management operations."""

        raw_token = self.issue_token().json()['token']
        self.client.logout()

        response = self.client.post(
            '/api/v1/auth/tokens',
            {'name': 'Nested token', 'scopes': ['read_api']},
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {raw_token}',
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(AccessToken.objects.count(), 1)

    def test_session_token_creation_requires_csrf_protection(self):
        """Protect browser-session credential issuance from cross-site requests."""

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)

        response = csrf_client.post(
            '/api/v1/auth/tokens',
            {'name': 'Cross-site token', 'scopes': ['read_api']},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(AccessToken.objects.exists())

    def test_bearer_token_authenticates_current_user_and_records_last_use(self):
        """Authenticate REST requests with the unified bearer-token format."""

        issued = self.issue_token().json()
        self.client.logout()

        response = self.client.get('/api/v1/auth/me', HTTP_AUTHORIZATION=f"Bearer {issued['token']}")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['username'], 'researcher')
        self.assertEqual(body['authentication_method'], 'access_token')
        self.assertEqual(body['access_token']['id'], issued['id'])
        self.assertIsNotNone(AccessToken.objects.get().last_used_at)

    def test_revoked_access_token_stops_authenticating(self):
        """Apply browser revocation immediately to subsequent API requests."""

        issued = self.issue_token().json()

        revoke_response = self.client.delete(f"/api/v1/auth/tokens/{issued['id']}")
        self.client.logout()
        authenticated_response = self.client.get('/api/v1/auth/me', HTTP_AUTHORIZATION=f"Bearer {issued['token']}")

        self.assertEqual(revoke_response.status_code, 204)
        self.assertEqual(authenticated_response.status_code, 401)
        self.assertIsNotNone(AccessToken.objects.get().revoked_at)

    def test_bearer_token_can_revoke_only_itself(self):
        """Support CLI logout without granting bearer token management."""

        first = self.issue_token(name='First').json()
        second = self.issue_token(name='Second').json()
        self.client.logout()

        other_response = self.client.delete(f"/api/v1/auth/tokens/{second['id']}", HTTP_AUTHORIZATION=f"Bearer {first['token']}")
        self_response = self.client.delete(f"/api/v1/auth/tokens/{first['id']}", HTTP_AUTHORIZATION=f"Bearer {first['token']}")

        self.assertEqual(other_response.status_code, 403)
        self.assertEqual(other_response.json()['code'], 'token_self_revocation_required')
        self.assertEqual(self_response.status_code, 204)
        self.assertIsNotNone(AccessToken.objects.get(pk=first['id']).revoked_at)
        self.assertIsNone(AccessToken.objects.get(pk=second['id']).revoked_at)

    def test_session_token_revocation_remains_csrf_protected(self):
        """Keep browser revocation protected after enabling self-revocation."""

        issued = self.issue_token().json()
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)

        response = csrf_client.delete(f"/api/v1/auth/tokens/{issued['id']}")

        self.assertEqual(response.status_code, 403)
        self.assertIsNone(AccessToken.objects.get(pk=issued['id']).revoked_at)

    def test_dataset_boundary_is_persisted_by_immutable_identity(self):
        """Bind a token to the dataset UUID while displaying its current path."""

        dataset = self.create_dataset()

        response = self.issue_token(dataset_id=str(dataset.id))

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body['resource_boundary'], 'dataset')
        self.assertEqual(body['dataset_id'], str(dataset.id))
        self.assertEqual(body['dataset_path'], 'researcher/images')

    def test_token_expiry_cannot_exceed_configured_maximum(self):
        """Reject dashboard requests beyond the installation lifetime cap."""

        with override_settings(NIYAN_ACCESS_TOKEN_MAX_DAYS=1):
            response = self.issue_token(expires_at=(timezone.now() + timedelta(days=2)).isoformat())

        self.assertEqual(response.status_code, 422)
        self.assertFalse(AccessToken.objects.exists())

    def test_bearer_scope_and_dataset_boundary_restrict_dataset_api(self):
        """Intersect current user access with token scope and dataset UUID."""

        first_dataset = self.create_dataset(slug='first', name='First')
        second_dataset = self.create_dataset(slug='second', name='Second')
        token_response = self.issue_token(scopes=['read_api'], dataset_id=str(first_dataset.id))
        raw_token = token_response.json()['token']
        self.client.logout()

        allowed_response = self.client.get(f'/api/v1/datasets/{first_dataset.id}', HTTP_AUTHORIZATION=f'Bearer {raw_token}')
        other_response = self.client.get(f'/api/v1/datasets/{second_dataset.id}', HTTP_AUTHORIZATION=f'Bearer {raw_token}')
        update_response = self.client.patch(
            f'/api/v1/datasets/{first_dataset.id}',
            {'name': 'Changed'},
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {raw_token}',
        )
        list_response = self.client.get(
            '/api/v1/datasets',
            {'namespace_id': str(self.user.personal_namespace.id)},
            HTTP_AUTHORIZATION=f'Bearer {raw_token}',
        )

        self.assertEqual(allowed_response.status_code, 200)
        self.assertEqual(other_response.status_code, 403)
        self.assertEqual(update_response.status_code, 403)
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual([item['id'] for item in list_response.json()['items']], [str(first_dataset.id)])

    def test_api_scope_allows_dataset_mutation(self):
        """Treat the broad API scope as including read-only API access."""

        dataset = self.create_dataset()
        raw_token = self.issue_token(scopes=['api']).json()['token']
        self.client.logout()
        api_client = Client(enforce_csrf_checks=True)

        read_response = api_client.get(f'/api/v1/datasets/{dataset.id}', HTTP_AUTHORIZATION=f'Bearer {raw_token}')
        update_response = api_client.patch(
            f'/api/v1/datasets/{dataset.id}',
            {'name': 'Updated'},
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {raw_token}',
        )

        self.assertEqual(read_response.status_code, 200)
        self.assertEqual(update_response.status_code, 200, update_response.content)
        self.assertEqual(update_response.json()['name'], 'Updated')


class BrowserSessionApiTests(TestCase):
    """Verify same-origin browser login, CSRF, logout, and user discovery."""

    def setUp(self):
        """Create one active user with a known password."""

        self.user = User.objects.create_user(username='researcher', password='correct horse battery staple', first_name='Ada', last_name='Lovelace')
        self.client = Client(enforce_csrf_checks=True)

    def csrf_token(self):
        """Initialize and return the browser client's CSRF token."""

        response = self.client.get('/api/v1/auth/csrf')
        self.assertEqual(response.status_code, 200)
        return response.json()['csrf_token']

    def test_browser_login_requires_csrf_and_creates_session(self):
        """Protect credential submission and return current account state."""

        rejected = self.client.post('/api/v1/auth/session', {'username': 'researcher', 'password': 'correct horse battery staple'}, content_type='application/json')
        token = self.csrf_token()
        accepted = self.client.post(
            '/api/v1/auth/session',
            {'username': 'researcher', 'password': 'correct horse battery staple'},
            content_type='application/json',
            HTTP_X_CSRFTOKEN=token,
        )
        current = self.client.get('/api/v1/auth/me')

        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()['display_name'], 'Ada Lovelace')
        self.assertFalse(accepted.json()['is_superuser'])
        self.assertEqual(current.status_code, 200)

    def test_invalid_browser_credentials_do_not_create_session(self):
        """Return one stable failure without revealing which credential failed."""

        response = self.client.post(
            '/api/v1/auth/session',
            {'username': 'researcher', 'password': 'incorrect'},
            content_type='application/json',
            HTTP_X_CSRFTOKEN=self.csrf_token(),
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 'invalid_credentials')

    def test_browser_logout_requires_csrf_and_ends_session(self):
        """Prevent cross-site logout and invalidate current-account state."""

        self.client.force_login(self.user)
        rejected = self.client.delete('/api/v1/auth/session')
        token = self.csrf_token()
        accepted = self.client.delete('/api/v1/auth/session', HTTP_X_CSRFTOKEN=token)
        current = self.client.get('/api/v1/auth/me')

        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(accepted.status_code, 204)
        self.assertEqual(current.status_code, 401)

    def test_browser_user_search_is_bounded_and_session_only(self):
        """Support membership selectors without exposing users anonymously."""

        anonymous = self.client.get('/api/v1/auth/users', {'query': 're'})
        self.client.force_login(self.user)
        authenticated = self.client.get('/api/v1/auth/users', {'query': 'ada'})

        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(authenticated.status_code, 200)
        self.assertEqual(authenticated.json()['items'][0]['username'], 'researcher')

    def test_browser_user_can_change_email_after_password_confirmation(self):
        """Update account email without weakening password confirmation or CSRF."""

        self.client.force_login(self.user)
        response = self.client.patch(
            '/api/v1/auth/me/email',
            {'current_password': 'correct horse battery staple', 'email': 'ada@example.test'},
            content_type='application/json',
            HTTP_X_CSRFTOKEN=self.csrf_token(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['email'], 'ada@example.test')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'ada@example.test')

    def test_browser_email_change_rejects_wrong_password(self):
        """Keep possession of an authenticated browser session insufficient alone."""

        self.client.force_login(self.user)
        response = self.client.patch(
            '/api/v1/auth/me/email',
            {'current_password': 'wrong password', 'email': 'ada@example.test'},
            content_type='application/json',
            HTTP_X_CSRFTOKEN=self.csrf_token(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'invalid_current_password')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, '')

    def test_browser_user_can_change_password_and_keep_current_session(self):
        """Rotate the password while retaining the explicitly confirmed session."""

        self.client.force_login(self.user)
        response = self.client.patch(
            '/api/v1/auth/me/password',
            {'current_password': 'correct horse battery staple', 'new_password': 'a much newer horse battery staple'},
            content_type='application/json',
            HTTP_X_CSRFTOKEN=self.csrf_token(),
        )
        current = self.client.get('/api/v1/auth/me')

        self.assertEqual(response.status_code, 204)
        self.assertEqual(current.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('a much newer horse battery staple'))

    def test_browser_password_change_requires_csrf(self):
        """Reject cross-site credential rotation."""

        self.client.force_login(self.user)
        response = self.client.patch(
            '/api/v1/auth/me/password',
            {'current_password': 'correct horse battery staple', 'new_password': 'a much newer horse battery staple'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('correct horse battery staple'))


class DeviceAuthorizationApiTests(TestCase):
    """Verify the browser-assisted CLI login protocol."""

    def setUp(self):
        """Create a browser user and reset throttle state."""

        cache.clear()
        self.user = User.objects.create_user(username='researcher')
        self.browser = Client()
        self.browser.force_login(self.user)
        self.cli = Client()

    def start_authorization(self, **overrides):
        """Start one device authorization with valid defaults."""

        payload = {'name': 'HPC login node', 'scopes': ['api', 'write_repository']}
        payload.update(overrides)
        return self.cli.post('/api/v1/auth/device', payload, content_type='application/json')

    def test_device_flow_issues_same_access_token_type_after_browser_approval(self):
        """Approve a CLI request and exchange its private code exactly once."""

        started = self.start_authorization()
        self.assertEqual(started.status_code, 201)
        codes = started.json()

        review_response = self.browser.get(f"/api/v1/auth/device/{codes['user_code']}")
        approval_response = self.browser.post(
            f"/api/v1/auth/device/{codes['user_code']}",
            {'approve': True},
            content_type='application/json',
        )
        exchange_response = self.cli.post(
            '/api/v1/auth/device/token',
            {'device_code': codes['device_code']},
            content_type='application/json',
        )

        self.assertEqual(review_response.status_code, 200)
        self.assertEqual(review_response.json()['scopes'], ['api', 'write_repository'])
        self.assertEqual(approval_response.status_code, 200)
        self.assertEqual(approval_response.json()['status'], 'approved')
        self.assertEqual(exchange_response.status_code, 200)
        exchanged = exchange_response.json()
        self.assertTrue(exchanged['token'].startswith('niyan_'))
        self.assertEqual(exchanged['resource_boundary'], 'user')
        access_token = AccessToken.objects.get()
        self.assertEqual(access_token.origin, AccessToken.Origin.CLI)
        self.assertEqual(access_token.user, self.user)
        self.assertEqual(DeviceAuthorization.objects.get().status, DeviceAuthorization.Status.CONSUMED)

        replay_response = self.cli.post(
            '/api/v1/auth/device/token',
            {'device_code': codes['device_code']},
            content_type='application/json',
        )
        self.assertEqual(replay_response.status_code, 400)
        self.assertEqual(replay_response.json()['code'], 'device_code_used')

    def test_pending_poll_is_recorded_and_enforces_interval(self):
        """Distinguish a pending login and reject an immediate repeated poll."""

        codes = self.start_authorization().json()

        first_response = self.cli.post('/api/v1/auth/device/token', {'device_code': codes['device_code']}, content_type='application/json')
        second_response = self.cli.post('/api/v1/auth/device/token', {'device_code': codes['device_code']}, content_type='application/json')

        self.assertEqual(first_response.status_code, 202)
        self.assertEqual(first_response.json()['code'], 'authorization_pending')
        self.assertEqual(second_response.status_code, 429)
        self.assertEqual(second_response.json()['code'], 'slow_down')
        self.assertIsNotNone(DeviceAuthorization.objects.get().last_polled_at)

    def test_denied_device_authorization_cannot_issue_token(self):
        """Return an explicit denial without disclosing browser user data."""

        codes = self.start_authorization().json()
        self.browser.post(
            f"/api/v1/auth/device/{codes['user_code']}",
            {'approve': False},
            content_type='application/json',
        )

        response = self.cli.post('/api/v1/auth/device/token', {'device_code': codes['device_code']}, content_type='application/json')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'access_denied')
        self.assertFalse(AccessToken.objects.exists())

    def test_expired_device_authorization_cannot_be_reviewed_or_exchanged(self):
        """Invalidate both public and private codes after the short lifetime."""

        codes = self.start_authorization().json()
        DeviceAuthorization.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

        review_response = self.browser.get(f"/api/v1/auth/device/{codes['user_code']}")
        exchange_response = self.cli.post('/api/v1/auth/device/token', {'device_code': codes['device_code']}, content_type='application/json')

        self.assertEqual(review_response.status_code, 404)
        self.assertEqual(exchange_response.status_code, 400)
        self.assertEqual(exchange_response.json()['code'], 'expired_device_code')

    def test_dataset_login_resolves_path_to_immutable_boundary_at_approval(self):
        """Persist the dataset UUID selected by a human-facing login path."""

        dataset = Dataset.objects.create(namespace=self.user.personal_namespace, slug='images', name='Images', created_by=self.user)
        codes = self.start_authorization(dataset_path='researcher/images').json()

        approval_response = self.browser.post(
            f"/api/v1/auth/device/{codes['user_code']}",
            {'approve': True},
            content_type='application/json',
        )
        exchange_response = self.cli.post('/api/v1/auth/device/token', {'device_code': codes['device_code']}, content_type='application/json')

        self.assertEqual(approval_response.status_code, 200)
        self.assertEqual(exchange_response.status_code, 200)
        self.assertEqual(exchange_response.json()['resource_boundary'], 'dataset')
        self.assertEqual(exchange_response.json()['dataset_id'], str(dataset.id))
        self.assertEqual(AccessToken.objects.get().dataset_id, dataset.id)

    def test_browser_user_cannot_approve_an_inaccessible_dataset_path(self):
        """Keep requested dataset paths subject to current user authorization."""

        another_user = User.objects.create_user(username='another')
        Dataset.objects.create(namespace=another_user.personal_namespace, slug='private', name='Private', created_by=another_user)
        codes = self.start_authorization(dataset_path='another/private').json()

        response = self.browser.post(
            f"/api/v1/auth/device/{codes['user_code']}",
            {'approve': True},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'dataset_unavailable')
        self.assertEqual(DeviceAuthorization.objects.get().status, DeviceAuthorization.Status.PENDING)
        self.assertFalse(AccessToken.objects.exists())


class AccessTokenServiceTests(TestCase):
    """Verify credential rules below the HTTP layer."""

    def test_expired_token_is_inactive(self):
        """Represent expiry independently from explicit revocation."""

        user = User.objects.create_user(username='researcher')
        access_token, _ = create_access_token(user=user, name='Temporary', scopes=['read_api'], origin=AccessToken.Origin.MANUAL)
        AccessToken.objects.filter(pk=access_token.pk).update(expires_at=timezone.now() - timedelta(seconds=1))

        access_token.refresh_from_db()

        self.assertFalse(access_token.is_active())
