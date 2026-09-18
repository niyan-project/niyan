"""Read-only fsspec integration for Niyān datasets."""

from niyan.filesystem.core import NiyanFileSystem
from niyan.filesystem.errors import NiyanAuthenticationError, NiyanCompatibilityError, NiyanFileSystemError, NiyanIntegrityError, NiyanNotFoundError, NiyanPermissionError, NiyanTransferError

__all__ = [
    'NiyanAuthenticationError',
    'NiyanCompatibilityError',
    'NiyanFileSystem',
    'NiyanFileSystemError',
    'NiyanIntegrityError',
    'NiyanNotFoundError',
    'NiyanPermissionError',
    'NiyanTransferError',
]
