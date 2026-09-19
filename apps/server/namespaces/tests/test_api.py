from django.test import Client, TestCase

from accounts.models import User
from namespaces.models import Namespace, NamespaceMembership


class NamespaceApiTests(TestCase):
    """Verify path lookup without exposing inaccessible namespaces."""

    def setUp(self):
        """Create an authenticated user and API client."""

        self.user = User.objects.create_user(username='researcher')
        self.client = Client()
        self.client.force_login(self.user)

    def test_resolve_personal_namespace_returns_stable_identity(self):
        """Resolve the path-oriented CLI input to an immutable UUID."""

        response = self.client.get('/api/v1/namespaces/resolve', {'path': 'researcher'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                'id': str(self.user.personal_namespace.id),
                'path': 'researcher',
                'name': self.user.personal_namespace.name,
                'kind': 'personal',
            },
        )

    def test_resolve_nested_group_requires_visibility(self):
        """Hide group paths until membership grants namespace visibility."""

        laboratory = Namespace.objects.create(kind=Namespace.Kind.GROUP, name='Laboratory', slug='lab')
        imaging = Namespace.objects.create(kind=Namespace.Kind.GROUP, name='Imaging', slug='imaging', parent=laboratory)

        hidden_response = self.client.get('/api/v1/namespaces/resolve', {'path': 'lab/imaging'})
        NamespaceMembership.objects.create(namespace=laboratory, user=self.user, role=NamespaceMembership.Role.READER)
        visible_response = self.client.get('/api/v1/namespaces/resolve', {'path': 'lab/imaging'})

        self.assertEqual(hidden_response.status_code, 404)
        self.assertEqual(visible_response.status_code, 200)
        self.assertEqual(visible_response.json()['id'], str(imaging.id))

    def test_resolve_namespace_requires_authentication(self):
        """Reject anonymous namespace discovery."""

        self.client.logout()

        response = self.client.get('/api/v1/namespaces/resolve', {'path': 'researcher'})

        self.assertEqual(response.status_code, 401)

    def test_user_can_create_root_and_nested_groups(self):
        """Create browser-facing groups while retaining namespace identities."""

        root_response = self.client.post('/api/v1/namespaces', {'name': 'Helix Lab', 'slug': 'Helix'}, content_type='application/json')
        nested_response = self.client.post(
            '/api/v1/namespaces',
            {'name': 'Imaging', 'slug': 'Imaging', 'parent_id': root_response.json()['id']},
            content_type='application/json',
        )

        self.assertEqual(root_response.status_code, 201, root_response.content)
        self.assertEqual(root_response.json()['path'], 'helix')
        self.assertEqual(root_response.json()['role'], 'owner')
        self.assertEqual(nested_response.status_code, 201, nested_response.content)
        self.assertEqual(nested_response.json()['path'], 'helix/imaging')
        self.assertTrue(NamespaceMembership.objects.filter(namespace_id=nested_response.json()['id'], user=self.user, role='owner').exists())

    def test_browser_group_mutation_requires_csrf(self):
        """Protect group administration performed with a browser session."""

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)

        response = csrf_client.post('/api/v1/namespaces', {'name': 'Helix Lab', 'slug': 'helix'}, content_type='application/json')

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Namespace.objects.filter(kind=Namespace.Kind.GROUP).exists())

    def test_visible_namespace_list_includes_personal_and_groups(self):
        """Supply the dashboard with personal and group navigation entries."""

        group = Namespace.objects.create(kind=Namespace.Kind.GROUP, name='Laboratory', slug='lab')
        NamespaceMembership.objects.create(namespace=group, user=self.user, role=NamespaceMembership.Role.READER)

        response = self.client.get('/api/v1/namespaces')

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['path'] for item in response.json()['items']], ['lab', 'researcher'])

    def test_group_owner_can_rename_and_delete_empty_group(self):
        """Expose mutable group paths and irreversible empty-group deletion."""

        group = Namespace.objects.create(kind=Namespace.Kind.GROUP, name='Laboratory', slug='lab')
        NamespaceMembership.objects.create(namespace=group, user=self.user, role=NamespaceMembership.Role.OWNER)

        update_response = self.client.patch(f'/api/v1/namespaces/{group.id}', {'name': 'Renamed Lab', 'slug': 'renamed'}, content_type='application/json')
        delete_response = self.client.delete(f'/api/v1/namespaces/{group.id}')

        self.assertEqual(update_response.status_code, 200, update_response.content)
        self.assertEqual(update_response.json()['path'], 'renamed')
        self.assertEqual(delete_response.status_code, 204)
        self.assertFalse(Namespace.objects.filter(pk=group.pk).exists())

    def test_group_with_children_cannot_be_deleted(self):
        """Require owners to empty a group before permanent deletion."""

        group = Namespace.objects.create(kind=Namespace.Kind.GROUP, name='Laboratory', slug='lab')
        NamespaceMembership.objects.create(namespace=group, user=self.user, role=NamespaceMembership.Role.OWNER)
        Namespace.objects.create(kind=Namespace.Kind.GROUP, name='Imaging', slug='imaging', parent=group)

        response = self.client.delete(f'/api/v1/namespaces/{group.id}')

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'group_not_empty')

    def test_group_membership_crud_preserves_last_direct_owner(self):
        """Manage direct members without allowing an ownerless group."""

        group = Namespace.objects.create(kind=Namespace.Kind.GROUP, name='Laboratory', slug='lab')
        owner_membership = NamespaceMembership.objects.create(namespace=group, user=self.user, role=NamespaceMembership.Role.OWNER)
        colleague = User.objects.create_user(username='colleague')

        create_response = self.client.post(
            f'/api/v1/namespaces/{group.id}/memberships',
            {'username': colleague.username, 'role': 'reader'},
            content_type='application/json',
        )
        membership_id = create_response.json()['id']
        update_response = self.client.patch(
            f'/api/v1/namespaces/{group.id}/memberships/{membership_id}',
            {'role': 'maintainer'},
            content_type='application/json',
        )
        last_owner_response = self.client.delete(f'/api/v1/namespaces/{group.id}/memberships/{owner_membership.id}')
        delete_response = self.client.delete(f'/api/v1/namespaces/{group.id}/memberships/{membership_id}')

        self.assertEqual(create_response.status_code, 201, create_response.content)
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.json()['role'], 'maintainer')
        self.assertEqual(last_owner_response.status_code, 409)
        self.assertEqual(delete_response.status_code, 204)

    def test_group_membership_requires_an_exact_existing_active_username(self):
        """Avoid ambiguous user selection and pending invitation state."""

        group = Namespace.objects.create(kind=Namespace.Kind.GROUP, name='Laboratory', slug='lab')
        NamespaceMembership.objects.create(namespace=group, user=self.user, role=NamespaceMembership.Role.OWNER)
        User.objects.create_user(username='colleague', is_active=False)

        unknown_response = self.client.post(
            f'/api/v1/namespaces/{group.id}/memberships',
            {'username': 'COLLEAGUE', 'role': 'reader'},
            content_type='application/json',
        )
        inactive_response = self.client.post(
            f'/api/v1/namespaces/{group.id}/memberships',
            {'username': 'colleague', 'role': 'reader'},
            content_type='application/json',
        )

        self.assertEqual(unknown_response.status_code, 404)
        self.assertEqual(inactive_response.status_code, 404)
        self.assertEqual(NamespaceMembership.objects.filter(namespace=group).count(), 1)

    def test_non_owner_cannot_discover_group_membership_administration(self):
        """Hide direct membership metadata from a visible non-owner."""

        group = Namespace.objects.create(kind=Namespace.Kind.GROUP, name='Laboratory', slug='lab')
        NamespaceMembership.objects.create(namespace=group, user=self.user, role=NamespaceMembership.Role.READER)

        response = self.client.get(f'/api/v1/namespaces/{group.id}/memberships')

        self.assertEqual(response.status_code, 404)
