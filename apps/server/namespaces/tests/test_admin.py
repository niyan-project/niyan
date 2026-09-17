from django.contrib import admin
from django.test import TestCase

from accounts.models import User
from namespaces.admin import NamespaceAdmin, NamespaceMembershipInline
from namespaces.models import Namespace


class NamespaceAdminTests(TestCase):
    """Verify namespace admin pages expose only valid membership controls."""

    def setUp(self):
        """Construct the registered model administrator for each test."""

        self.model_admin = NamespaceAdmin(Namespace, admin.site)

    def test_group_namespace_includes_membership_editor(self):
        """Inline Niyān membership on persisted group namespace pages."""

        namespace = Namespace.objects.create(kind=Namespace.Kind.GROUP, name='Laboratory', slug='lab')

        self.assertEqual(self.model_admin.get_inlines(request=None, obj=namespace), (NamespaceMembershipInline,))

    def test_personal_namespace_omits_membership_editor(self):
        """Avoid offering group membership controls on personal namespaces."""

        user = User.objects.create_user(username='researcher')

        self.assertEqual(self.model_admin.get_inlines(request=None, obj=user.personal_namespace), ())
