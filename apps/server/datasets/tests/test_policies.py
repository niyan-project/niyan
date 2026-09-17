from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import Client, TestCase, override_settings

from accounts.models import User
from datasets.models import Dataset, DatasetGrant
from datasets.policies import can_create_dataset, can_delete_dataset, can_manage_dataset_grants, can_read_dataset, can_update_dataset, can_write_repository, get_dataset_role
from namespaces.models import Namespace, NamespaceMembership


class DatasetPolicyTests(TestCase):
    """Verify one role policy across ownership, memberships, and grants."""

    def setUp(self):
        """Create users, nested groups, and one group-owned dataset."""

        self.user = User.objects.create_user(username='researcher')
        self.other_user = User.objects.create_user(username='collaborator')
        self.root_group = Namespace.objects.create(kind=Namespace.Kind.GROUP, name='Laboratory', slug='lab')
        self.child_group = Namespace.objects.create(kind=Namespace.Kind.GROUP, name='Imaging', slug='imaging', parent=self.root_group)
        self.dataset = Dataset.objects.create(namespace=self.child_group, slug='slides', name='Slides', created_by=self.user)

    def test_ancestor_membership_flows_into_nested_dataset(self):
        """Inherit group membership downward but preserve its assigned role."""

        NamespaceMembership.objects.create(namespace=self.root_group, user=self.user, role=NamespaceMembership.Role.CONTRIBUTOR)

        self.assertEqual(get_dataset_role(user=self.user, dataset=self.dataset), NamespaceMembership.Role.CONTRIBUTOR)
        self.assertTrue(can_read_dataset(user=self.user, dataset=self.dataset))
        self.assertTrue(can_write_repository(user=self.user, dataset=self.dataset))
        self.assertFalse(can_update_dataset(user=self.user, dataset=self.dataset))

    def test_direct_user_grant_contributes_its_role(self):
        """Allow a dataset owner to collaborate outside its namespace."""

        DatasetGrant.objects.create(dataset=self.dataset, user=self.other_user, role=NamespaceMembership.Role.MAINTAINER)

        self.assertTrue(can_read_dataset(user=self.other_user, dataset=self.dataset))
        self.assertTrue(can_write_repository(user=self.other_user, dataset=self.dataset))
        self.assertTrue(can_update_dataset(user=self.other_user, dataset=self.dataset))
        self.assertFalse(can_manage_dataset_grants(user=self.other_user, dataset=self.dataset))

    def test_group_grant_is_capped_by_membership_and_grant_roles(self):
        """Use the lower of a group member role and the dataset grant role."""

        external_group = Namespace.objects.create(kind=Namespace.Kind.GROUP, name='External', slug='external')
        group_owner = User.objects.create_user(username='group-owner')
        group_reader = User.objects.create_user(username='group-reader')
        NamespaceMembership.objects.create(namespace=external_group, user=group_owner, role=NamespaceMembership.Role.OWNER)
        NamespaceMembership.objects.create(namespace=external_group, user=group_reader, role=NamespaceMembership.Role.READER)
        DatasetGrant.objects.create(dataset=self.dataset, group_namespace=external_group, role=NamespaceMembership.Role.CONTRIBUTOR)

        self.assertEqual(get_dataset_role(user=group_owner, dataset=self.dataset), NamespaceMembership.Role.CONTRIBUTOR)
        self.assertEqual(get_dataset_role(user=group_reader, dataset=self.dataset), NamespaceMembership.Role.READER)

    def test_highest_independent_access_path_wins(self):
        """Do not let a lower group grant subtract a stronger direct grant."""

        NamespaceMembership.objects.create(namespace=self.root_group, user=self.user, role=NamespaceMembership.Role.READER)
        DatasetGrant.objects.create(dataset=self.dataset, user=self.user, role=NamespaceMembership.Role.OWNER)

        self.assertEqual(get_dataset_role(user=self.user, dataset=self.dataset), NamespaceMembership.Role.OWNER)
        self.assertTrue(can_manage_dataset_grants(user=self.user, dataset=self.dataset))
        self.assertTrue(can_delete_dataset(user=self.user, dataset=self.dataset))

    def test_maintainer_can_create_dataset_in_group(self):
        """Apply the role matrix to group-level dataset creation."""

        NamespaceMembership.objects.create(namespace=self.root_group, user=self.user, role=NamespaceMembership.Role.MAINTAINER)

        self.assertTrue(can_create_dataset(user=self.user, namespace=self.child_group))


class DatasetGrantApiTests(TestCase):
    """Verify owner-managed dataset grant CRUD and resulting visibility."""

    def setUp(self):
        """Create an owner, collaborator, dataset, and repository root."""

        self.owner = User.objects.create_user(username='owner')
        self.collaborator = User.objects.create_user(username='collaborator')
        self.client = Client()
        self.client.force_login(self.owner)
        self.repository_directory = TemporaryDirectory()
        self.settings_override = override_settings(REPOSITORIES_ROOT=Path(self.repository_directory.name))
        self.settings_override.enable()
        response = self.client.post(
            '/api/v1/datasets',
            {'namespace_id': str(self.owner.personal_namespace.id), 'slug': 'images', 'name': 'Images'},
            content_type='application/json',
        )
        self.dataset = Dataset.objects.get(pk=response.json()['id'])

    def tearDown(self):
        """Restore repository settings and remove temporary repositories."""

        self.settings_override.disable()
        self.repository_directory.cleanup()

    def test_owner_can_create_update_list_and_delete_user_grant(self):
        """Expose complete grant CRUD without a second authorization model."""

        create_response = self.client.post(
            f'/api/v1/datasets/{self.dataset.id}/grants',
            {'user_id': self.collaborator.id, 'role': 'reader'},
            content_type='application/json',
        )
        self.assertEqual(create_response.status_code, 201)
        grant_id = create_response.json()['id']

        list_response = self.client.get(f'/api/v1/datasets/{self.dataset.id}/grants')
        update_response = self.client.patch(
            f'/api/v1/datasets/{self.dataset.id}/grants/{grant_id}',
            {'role': 'contributor'},
            content_type='application/json',
        )
        self.client.force_login(self.collaborator)
        visible_response = self.client.get(f'/api/v1/datasets/{self.dataset.id}')
        self.client.force_login(self.owner)
        delete_response = self.client.delete(f'/api/v1/datasets/{self.dataset.id}/grants/{grant_id}')

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()['count'], 1)
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.json()['role'], 'contributor')
        self.assertEqual(visible_response.status_code, 200)
        self.assertEqual(delete_response.status_code, 204)
        self.assertFalse(DatasetGrant.objects.exists())

    def test_reader_cannot_manage_grants(self):
        """Reserve access-list administration for dataset owners."""

        DatasetGrant.objects.create(dataset=self.dataset, user=self.collaborator, role=NamespaceMembership.Role.READER)
        self.client.force_login(self.collaborator)

        response = self.client.post(
            f'/api/v1/datasets/{self.dataset.id}/grants',
            {'user_id': self.owner.id, 'role': 'reader'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 404)

    def test_group_grant_rejects_personal_namespace(self):
        """Keep personal namespaces from becoming accidental group principals."""

        response = self.client.post(
            f'/api/v1/datasets/{self.dataset.id}/grants',
            {'group_namespace_id': str(self.collaborator.personal_namespace.id), 'role': 'reader'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(DatasetGrant.objects.exists())

    def test_owner_can_grant_access_to_niyan_group(self):
        """Expose Niyān namespaces, rather than Django groups, as principals."""

        group = Namespace.objects.create(kind=Namespace.Kind.GROUP, name='Collaborators', slug='collaborators')

        response = self.client.post(
            f'/api/v1/datasets/{self.dataset.id}/grants',
            {'group_namespace_id': str(group.id), 'role': 'reader'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['principal_type'], 'group')
        self.assertEqual(response.json()['principal_label'], 'collaborators')

    def test_duplicate_grant_returns_conflict(self):
        """Map database-enforced duplicate grants to a stable conflict."""

        DatasetGrant.objects.create(dataset=self.dataset, user=self.collaborator, role=NamespaceMembership.Role.READER)

        response = self.client.post(
            f'/api/v1/datasets/{self.dataset.id}/grants',
            {'user_id': self.collaborator.id, 'role': 'contributor'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'grant_conflict')
