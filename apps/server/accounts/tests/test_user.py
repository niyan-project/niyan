from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from accounts.models import User


class UserModelTests(TestCase):
    """Verify Niyān's configured user model and manager behavior."""

    def test_project_uses_niyan_user_model(self):
        """Use the Niyān-owned model as Django's active user model."""

        self.assertIs(get_user_model(), User)

    def test_user_manager_creates_user_with_hashed_password(self):
        """Create users through Django's manager without storing raw passwords."""

        user = User.objects.create_user(username='researcher', password='correct-horse-battery-staple')

        self.assertEqual(user.username, 'researcher')
        self.assertTrue(user.check_password('correct-horse-battery-staple'))


class UserAdminTests(SimpleTestCase):
    """Verify the custom user is manageable through Django admin."""

    def test_user_is_registered_with_admin(self):
        """Register the Niyān user model with the admin site."""

        self.assertTrue(admin.site.is_registered(User))
