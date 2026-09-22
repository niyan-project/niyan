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
            Permission.objects.get(content_type__app_label='accounts', codename='change_user'),
        )
        self.operator.groups.add(self.permission_group)
        self.client = Client()
        self.client.force_login(self.operator)

    def test_current_user_exposes_stable_system_permissions(self):
        """Let the SPA build system navigation without exposing Django codenames."""

        response = self.client.get('/api/v1/auth/me')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['is_staff'])
        self.assertEqual(response.json()['system_permissions'], ['users.view', 'users.add', 'users.change', 'staff.view', 'staff.change'])

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

    def test_operator_can_edit_user_profile_and_staff_state(self):
        """Delegate routine account administration without Django admin access."""

        target = User.objects.create_user(username='researcher', email='old@example.org')
        response = self.client.patch(
            f'/api/v1/system/users/{target.id}',
            {'email': 'new@example.org', 'first_name': 'Ada', 'last_name': 'Lovelace', 'is_staff': True},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200, response.content)
        target.refresh_from_db()
        self.assertEqual(target.get_full_name(), 'Ada Lovelace')
        self.assertEqual(target.email, 'new@example.org')
        self.assertTrue(target.is_staff)
        staff_response = self.client.get('/api/v1/system/staff')
        self.assertEqual(staff_response.status_code, 200)
        self.assertIn('researcher', [item['username'] for item in staff_response.json()['items']])

    def test_delegated_operator_cannot_edit_a_superuser(self):
        """Reserve superuser account changes for another superuser."""

        superuser = User.objects.create_superuser(username='root', password='irrelevant')
        response = self.client.patch(f'/api/v1/system/users/{superuser.id}', {'is_staff': False}, content_type='application/json')

        self.assertEqual(response.status_code, 403)
        superuser.refresh_from_db()
        self.assertTrue(superuser.is_staff)

    def test_superuser_can_manage_permission_groups(self):
        """Expose Django groups through stable System permission names."""

        superuser = User.objects.create_superuser(username='root', password='irrelevant')
        self.client.force_login(superuser)
        created = self.client.post(
            '/api/v1/system/permission-groups',
            {'name': 'Dataset auditors', 'permissions': ['datasets.view', 'users.view']},
            content_type='application/json',
        )
        self.assertEqual(created.status_code, 201, created.content)
        group_id = created.json()['id']
        listed = self.client.get('/api/v1/system/permission-groups')
        self.assertEqual(listed.status_code, 200)
        self.assertIn('permission_groups.change', listed.json()['available_permissions'])
        created_group = next(item for item in listed.json()['items'] if item['id'] == group_id)
        self.assertEqual(set(created_group['permissions']), {'datasets.view', 'users.view', 'staff.view'})

        updated = self.client.put(
            f'/api/v1/system/permission-groups/{group_id}',
            {'name': 'Dataset managers', 'permissions': ['datasets.view', 'datasets.change']},
            content_type='application/json',
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()['name'], 'Dataset managers')
        deleted = self.client.delete(f'/api/v1/system/permission-groups/{group_id}')
        self.assertEqual(deleted.status_code, 204)

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
