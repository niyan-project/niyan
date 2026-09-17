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
