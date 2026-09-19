import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase

from datasets.repositories import GitRepositoryStore, ProvisionedRepository, RepositoryDeletionError, RepositoryProvisioningError, RepositoryReadError


class RepositoryStoreFailureTests(SimpleTestCase):
    """Verify bare repository lifecycle operations fail safely and recoverably."""

    def setUp(self):
        """Create one isolated repository root and dataset identity."""

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = GitRepositoryStore(self.root)
        self.dataset_id = uuid4()

    def tearDown(self):
        """Remove isolated repository storage."""

        self.temporary_directory.cleanup()

    def test_provisioned_rollback_handles_directory_symlink_and_scope_checks(self):
        """Remove only direct children created by the provisioning operation."""

        directory = self.store.path_for(self.dataset_id)
        directory.mkdir()
        ProvisionedRepository(root=self.root, path=directory).rollback()
        self.assertFalse(directory.exists())

        target = self.root / 'target'
        target.mkdir()
        link = self.store.path_for(self.dataset_id)
        link.symlink_to(target, target_is_directory=True)
        ProvisionedRepository(root=self.root, path=link).rollback()
        self.assertFalse(link.exists())
        self.assertTrue(target.exists())

        for path in (self.root, self.root.parent / 'outside.git'):
            with self.subTest(path=path), self.assertRaisesRegex(RepositoryProvisioningError, 'outside'):
                ProvisionedRepository(root=self.root, path=path).rollback()

    def test_create_rejects_existing_path_and_cleans_failed_git_directory(self):
        """Never overwrite a repository and remove unpublished temporary state."""

        final = self.store.path_for(self.dataset_id)
        final.mkdir()
        with self.assertRaisesRegex(RepositoryProvisioningError, 'already exists'):
            self.store.create(self.dataset_id)
        final.rmdir()

        with patch('datasets.repositories.subprocess.run', side_effect=OSError('git unavailable')):
            with self.assertRaisesRegex(RepositoryProvisioningError, 'could not initialize'):
                self.store.create(self.dataset_id)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_existing_path_rejects_missing_and_linked_repositories(self):
        """Open only a real direct child beneath the configured root."""

        with self.assertRaisesRegex(RepositoryReadError, 'unavailable'):
            self.store.existing_path(self.dataset_id)
        target = self.root / 'target'
        target.mkdir()
        self.store.path_for(self.dataset_id).symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(RepositoryReadError, 'unavailable'):
            self.store.existing_path(self.dataset_id)

    def test_create_and_delete_require_available_root(self):
        """Reject lifecycle mutations when repository storage is unavailable."""

        missing = GitRepositoryStore(self.root / 'missing')
        with self.assertRaises(RepositoryProvisioningError):
            missing.create(self.dataset_id)
        with self.assertRaises(RepositoryDeletionError):
            missing.delete(self.dataset_id)

    def test_delete_handles_missing_interrupted_and_inconsistent_states(self):
        """Resume UUID-scoped deletion without accepting ambiguous storage state."""

        self.store.delete(self.dataset_id, allow_missing=True)
        with self.assertRaisesRegex(RepositoryDeletionError, 'unavailable'):
            self.store.delete(self.dataset_id)

        staged = self.root / f'.{self.dataset_id}.deleting'
        staged.mkdir()
        self.store.delete(self.dataset_id)
        self.assertFalse(staged.exists())

        final = self.store.path_for(self.dataset_id)
        final.mkdir()
        staged.mkdir()
        with self.assertRaisesRegex(RepositoryDeletionError, 'inconsistent'):
            self.store.delete(self.dataset_id)

    def test_delete_translates_filesystem_failure(self):
        """Preserve a sanitized deletion error when recursive removal fails."""

        final = self.store.path_for(self.dataset_id)
        final.mkdir()
        with patch('datasets.repositories.shutil.rmtree', side_effect=OSError('private detail')):
            with self.assertRaisesRegex(RepositoryDeletionError, 'could not be deleted'):
                self.store.delete(self.dataset_id)


if __name__ == '__main__':
    unittest.main()
