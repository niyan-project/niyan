from django.contrib.auth.models import Group, Permission
from django.test import Client, TestCase

from accounts.models import User
from datasets.models import AuditEvent


class SystemApiTests(TestCase):
    """Verify permission-backed installation administration."""

    def setUp(self):
        """Create a staff operator whose authority comes from a Django group."""

        self.operator = User.objects.create_user(username='operator', password='operator-password', is_staff=True)
        self.permission_group = Group.objects.create(name='Account operators')
        self.permission_group.permissions.add(
            Permission.objects.get(content_type__app_label='accounts', codename='view_user'),
            Permission.objects.get(content_type__app_label='accounts', codename='add_user'),
        )
        self.operator.groups.add(self.permission_group)
        self.client = Client()
        self.client.force_login(self.operator)

    def test_current_user_exposes_stable_system_permissions(self):
        """Let the SPA build system navigation without exposing Django codenames."""

        response = self.client.get('/api/v1/auth/me')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['is_staff'])
        self.assertEqual(response.json()['system_permissions'], ['users.view', 'users.add'])

    def test_staff_operator_can_list_and_create_users(self):
        """Provision an active user and its personal namespace from the system API."""

        list_response = self.client.get('/api/v1/system/users')
        create_response = self.client.post(
            '/api/v1/system/users',
            {
                'username': 'new-researcher',
                'password': 'Niyan-Lab-7Hawk-Mosaic!',
                'email': 'researcher@example.org',
                'first_name': 'New',
                'last_name': 'Researcher',
            },
            content_type='application/json',
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()['items'][0]['username'], 'operator')
        self.assertEqual(create_response.status_code, 201, create_response.content)
        created = User.objects.get(username='new-researcher')
        self.assertTrue(created.is_active)
        self.assertFalse(created.is_staff)
        self.assertEqual(created.personal_namespace.path, 'new-researcher')
        self.assertTrue(created.check_password('Niyan-Lab-7Hawk-Mosaic!'))
        self.assertTrue(AuditEvent.objects.filter(action='user.created', actor_user_id=self.operator.id, payload__created_user_id=created.id).exists())

    def test_non_staff_user_cannot_use_django_permissions_as_product_authority(self):
        """Require staff status in addition to an assigned model permission."""

        ordinary_user = User.objects.create_user(username='ordinary')
        ordinary_user.user_permissions.add(Permission.objects.get(content_type__app_label='accounts', codename='view_user'))
        self.client.force_login(ordinary_user)

        current_response = self.client.get('/api/v1/auth/me')
        list_response = self.client.get('/api/v1/system/users')

        self.assertEqual(current_response.json()['system_permissions'], [])
        self.assertEqual(list_response.status_code, 403)

    def test_user_creation_rejects_invalid_details_without_partial_account(self):
        """Keep user and personal-namespace creation atomic on validation failure."""

        response = self.client.post(
            '/api/v1/system/users',
            {'username': 'invalid username', 'password': 'short'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 422)
        self.assertFalse(User.objects.filter(username='invalid username').exists())

    def test_user_creation_remains_csrf_protected(self):
        """Reject cross-site account provisioning despite a valid browser session."""

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.operator)

        response = csrf_client.post(
            '/api/v1/system/users',
            {'username': 'cross-site', 'password': 'Niyan-Lab-7Hawk-Mosaic!'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(username='cross-site').exists())
