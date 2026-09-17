import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.test import TestCase, TransactionTestCase

from accounts.models import User
from datasets.models import Dataset
from datasets.repositories import GitRepositoryStore, RepositoryProvisioningError
from datasets.services import DatasetPathConflict, create_dataset
from namespaces.models import Namespace


class DatasetCreationTests(TestCase):
    """Verify dataset records and bare Git repositories are created together."""

    def setUp(self):
        """Create an owner and personal namespace for each test."""

        self.user = User.objects.create_user(username='researcher')
        self.namespace = self.user.personal_namespace

    def test_create_dataset_initializes_bare_repository_on_main(self):
        """Publish a valid bare repository only after dataset validation succeeds."""

        with TemporaryDirectory() as repository_root:
            store = GitRepositoryStore(repository_root)
            dataset = create_dataset(namespace=self.namespace, slug='Images', name='Images', created_by=self.user, repository_store=store)
            repository_path = store.path_for(dataset.id)
            head = subprocess.run(
                ['git', '--git-dir', str(repository_path), 'symbolic-ref', 'HEAD'],
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertEqual(dataset.slug, 'images')
            self.assertTrue(repository_path.is_dir())
            self.assertEqual(head.stdout.strip(), 'refs/heads/main')

    def test_create_dataset_rejects_another_users_namespace(self):
        """Prevent dataset creation outside the caller's personal namespace."""

        another_user = User.objects.create_user(username='intruder')
        with TemporaryDirectory() as repository_root:
            with self.assertRaises(PermissionDenied):
                create_dataset(namespace=self.namespace, slug='images', name='Images', created_by=another_user, repository_store=GitRepositoryStore(repository_root))

        self.assertFalse(Dataset.objects.exists())

    def test_create_dataset_rejects_existing_dataset_path(self):
        """Prevent two datasets from sharing one namespace route."""

        with TemporaryDirectory() as repository_root:
            store = GitRepositoryStore(repository_root)
            create_dataset(namespace=self.namespace, slug='images', name='Images', created_by=self.user, repository_store=store)
            with self.assertRaises(DatasetPathConflict):
                create_dataset(namespace=self.namespace, slug='images', name='Other Images', created_by=self.user, repository_store=store)

    def test_repository_failure_rolls_back_dataset_record(self):
        """Avoid a visible dataset when the repository root is unavailable."""

        with TemporaryDirectory() as temporary_directory:
            missing_root = Path(temporary_directory) / 'missing'
            with self.assertRaises(RepositoryProvisioningError):
                create_dataset(namespace=self.namespace, slug='images', name='Images', created_by=self.user, repository_store=GitRepositoryStore(missing_root))

        self.assertFalse(Dataset.objects.exists())


class DatasetTransactionBoundaryTests(TransactionTestCase):
    """Verify repository creation owns the database commit boundary."""

    def test_create_dataset_rejects_a_wider_database_transaction(self):
        """Prevent a later outer rollback from orphaning a completed repository."""

        user = User.objects.create_user(username='researcher')
        namespace = user.personal_namespace

        with TemporaryDirectory() as repository_root:
            with transaction.atomic():
                with self.assertRaises(RuntimeError):
                    create_dataset(namespace=namespace, slug='images', name='Images', created_by=user, repository_store=GitRepositoryStore(repository_root))

        self.assertFalse(Dataset.objects.exists())
