from django.contrib import admin
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccessToken, DeviceAuthorization, User
from accounts.tokens import create_access_token, start_device_authorization


class AuthenticationAdminTests(TestCase):
    """Verify authentication metadata is safely inspectable in admin."""

    def setUp(self):
        """Create an authenticated administrator and authentication records."""

        self.user = User.objects.create_superuser(username='administrator', password='password')
        self.client.force_login(self.user)

    def test_authentication_models_are_registered(self):
        """Make both authentication lifecycle models discoverable in admin."""

        self.assertIn(AccessToken, admin.site._registry)
        self.assertIn(DeviceAuthorization, admin.site._registry)

    def test_access_token_admin_hides_secret_digest_and_disables_creation(self):
        """Show useful audit metadata without exposing or fabricating secrets."""

        access_token, _ = create_access_token(user=self.user, name='Workstation', scopes=['read_api'], origin=AccessToken.Origin.MANUAL)

        list_response = self.client.get(reverse('admin:accounts_accesstoken_changelist'))
        detail_response = self.client.get(reverse('admin:accounts_accesstoken_change', args=[access_token.pk]))
        add_response = self.client.get(reverse('admin:accounts_accesstoken_add'))

        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, 'Workstation')
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, access_token.fingerprint)
        self.assertNotContains(detail_response, access_token.secret_digest)
        self.assertEqual(add_response.status_code, 403)

    def test_device_authorization_admin_hides_private_digest_and_disables_creation(self):
        """Show the review lifecycle without exposing the CLI's private code."""

        authorization, _ = start_device_authorization(name='HPC login node', scopes=['read_api'])

        list_response = self.client.get(reverse('admin:accounts_deviceauthorization_changelist'))
        detail_response = self.client.get(reverse('admin:accounts_deviceauthorization_change', args=[authorization.pk]))
        add_response = self.client.get(reverse('admin:accounts_deviceauthorization_add'))

        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, 'HPC login node')
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, authorization.user_code)
        self.assertNotContains(detail_response, authorization.device_secret_digest)
        self.assertEqual(add_response.status_code, 403)
