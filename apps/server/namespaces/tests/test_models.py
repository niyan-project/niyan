from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.models import User
from namespaces.models import Namespace, NamespaceMembership


class NamespaceModelTests(TestCase):
    """Verify Niyān namespace identity, hierarchy, and membership rules."""

    def test_personal_namespace_has_stable_identity_and_normalized_path(self):
        """Normalize the mutable path without changing the namespace UUID."""

        user = User.objects.create_user(username='researcher')
        namespace = user.personal_namespace

        namespace_id = namespace.id
        namespace.slug = 'Renamed'
        namespace.full_clean()
        namespace.save()

        self.assertEqual(namespace.id, namespace_id)
        self.assertEqual(namespace.path, 'renamed')

    def test_nested_group_path_is_derived_from_ancestors(self):
        """Build nested group paths without storing a second path identity."""

        laboratory = Namespace.objects.create(kind=Namespace.Kind.GROUP, name='Laboratory', slug='lab')
        team = Namespace(kind=Namespace.Kind.GROUP, name='Imaging', slug='imaging', parent=laboratory)
        team.full_clean()
        team.save()

        self.assertEqual(team.path, 'lab/imaging')

    def test_group_namespace_rejects_parent_cycle(self):
        """Reject hierarchy changes that would make a namespace its own ancestor."""

        namespace = Namespace.objects.create(kind=Namespace.Kind.GROUP, name='Laboratory', slug='lab')
        namespace.parent = namespace

        with self.assertRaises(ValidationError):
            namespace.full_clean()

    def test_membership_rejects_personal_namespace(self):
        """Keep Niyān group roles separate from personal namespaces."""

        user = User.objects.create_user(username='researcher')
        namespace = user.personal_namespace
        membership = NamespaceMembership(namespace=namespace, user=user, role=NamespaceMembership.Role.OWNER)

        with self.assertRaises(ValidationError):
            membership.full_clean()

    def test_group_membership_does_not_use_django_auth_groups(self):
        """Store product membership without populating Django's groups relation."""

        user = User.objects.create_user(username='researcher')
        namespace = Namespace.objects.create(kind=Namespace.Kind.GROUP, name='Laboratory', slug='lab')
        membership = NamespaceMembership(namespace=namespace, user=user, role=NamespaceMembership.Role.OWNER)
        membership.full_clean()
        membership.save()

        self.assertEqual(user.namespace_memberships.get(), membership)
        self.assertFalse(user.groups.exists())

    def test_user_creation_provisions_exactly_one_personal_namespace(self):
        """Maintain the personal-namespace invariant through the user lifecycle."""

        user = User.objects.create_user(username='Researcher')

        self.assertEqual(user.personal_namespace.slug, 'researcher')
        self.assertEqual(Namespace.objects.filter(owner_user=user).count(), 1)
