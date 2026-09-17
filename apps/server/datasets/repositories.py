import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from django.conf import settings


class RepositoryProvisioningError(RuntimeError):
    """Report repository provisioning failure without exposing Git internals."""


class RepositoryDeletionError(RuntimeError):
    """Report repository deletion failure without exposing filesystem internals."""


class RepositoryReadError(RuntimeError):
    """Report a repository that cannot be safely opened for reading."""


@dataclass(frozen=True)
class ProvisionedRepository:
    """Track a repository created by one operation so it can be rolled back safely."""

    root: Path
    path: Path

    def rollback(self):
        """Remove only this operation's repository after a later failure.

        Raises
        ------
        RepositoryProvisioningError
            If the repository path is no longer a direct child of the configured root.
        """

        root = self.root.resolve()
        path = self.path.resolve(strict=False)
        if path == root or path.parent != root:
            raise RepositoryProvisioningError('Refusing to remove a repository outside the configured repository root.')
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)


class GitRepositoryStore:
    """Create UUID-addressed bare Git repositories beneath one configured root."""

    def __init__(self, root=None):
        """Initialize a repository store.

        Parameters
        ----------
        root : pathlib.Path or str, optional
            Repository root override used by tests and explicit callers.
        """

        self.root = Path(root if root is not None else settings.REPOSITORIES_ROOT).expanduser()

    def path_for(self, dataset_id):
        """Return the final repository path for a dataset UUID.

        Parameters
        ----------
        dataset_id : uuid.UUID or str
            Immutable dataset identifier.

        Returns
        -------
        pathlib.Path
            Direct child path beneath the configured repository root.
        """

        normalized_id = UUID(str(dataset_id))
        return self.root / f'{normalized_id}.git'

    def create(self, dataset_id):
        """Initialize and atomically publish a bare Git repository.

        Parameters
        ----------
        dataset_id : uuid.UUID or str
            Immutable dataset identifier.

        Returns
        -------
        ProvisionedRepository
            Handle that can remove this operation's repository if a later database commit fails.

        Raises
        ------
        RepositoryProvisioningError
            If the repository root is unavailable, the final path exists, or Git fails.
        """

        root = self.root.resolve()
        if not root.is_dir():
            raise RepositoryProvisioningError('The configured repository root is unavailable.')

        final_path = self.path_for(dataset_id)
        if final_path.exists():
            raise RepositoryProvisioningError('The dataset repository already exists.')

        temporary_path = Path(tempfile.mkdtemp(prefix=f'.{dataset_id}.', dir=root))
        try:
            subprocess.run(
                ['git', 'init', '--bare', '--initial-branch=main', str(temporary_path)],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            temporary_path.rename(final_path)
        except (OSError, subprocess.SubprocessError) as error:
            if temporary_path.exists():
                shutil.rmtree(temporary_path)
            raise RepositoryProvisioningError('Git could not initialize the dataset repository.') from error

        return ProvisionedRepository(root=root, path=final_path)

    def existing_path(self, dataset_id):
        """Return a safe existing bare repository path.

        Parameters
        ----------
        dataset_id : uuid.UUID or str
            Immutable dataset identifier.

        Returns
        -------
        pathlib.Path
            Resolved repository path beneath the configured root.

        Raises
        ------
        RepositoryReadError
            If the root or repository is missing, linked, or outside the root.
        """

        root = self.root.resolve()
        path = self.path_for(dataset_id)
        if not root.is_dir() or path.is_symlink() or not path.is_dir():
            raise RepositoryReadError('The dataset repository is unavailable.')
        resolved_path = path.resolve()
        if resolved_path.parent != root:
            raise RepositoryReadError('The dataset repository is unavailable.')
        return resolved_path

    def delete(self, dataset_id, *, allow_missing=False):
        """Permanently remove a dataset's bare Git repository.

        Parameters
        ----------
        dataset_id : uuid.UUID or str
            Immutable dataset identifier.
        allow_missing : bool, optional
            Permit an absent repository when resuming an interrupted deletion.

        Raises
        ------
        RepositoryDeletionError
            If repository storage is unavailable, inconsistent, or cannot be removed.
        """

        root = self.root.resolve()
        if not root.is_dir():
            raise RepositoryDeletionError('The configured repository root is unavailable.')

        final_path = self.path_for(dataset_id)
        staged_path = root / f'.{UUID(str(dataset_id))}.deleting'
        if final_path.exists() and staged_path.exists():
            raise RepositoryDeletionError('Repository deletion state is inconsistent.')

        try:
            if final_path.exists():
                final_path.rename(staged_path)
            elif not staged_path.exists():
                if allow_missing:
                    return
                raise RepositoryDeletionError('The dataset repository is unavailable.')

            # The UUID-derived staging name makes an interrupted deletion safe to retry.
            shutil.rmtree(staged_path)
        except RepositoryDeletionError:
            raise
        except OSError as error:
            raise RepositoryDeletionError('The dataset repository could not be deleted.') from error
