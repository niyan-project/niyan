"""Stable public errors raised by the Niyān filesystem client."""


class NiyanFileSystemError(Exception):
    """Base error for the importable filesystem client."""


class NiyanAuthenticationError(PermissionError, NiyanFileSystemError):
    """Report a missing, invalid, expired, or revoked credential."""


class NiyanPermissionError(PermissionError, NiyanFileSystemError):
    """Report an authenticated identity that lacks the requested access."""


class NiyanNotFoundError(FileNotFoundError, NiyanFileSystemError):
    """Report a missing non-secret installation resource.

    Parameters
    ----------
    message : str
        Public error detail.
    resource_kind : str, optional
        Non-secret category such as ``dataset``, ``revision``, or ``path``.
    """

    def __init__(self, message: str, *, resource_kind: str | None = None) -> None:
        self.resource_kind = resource_kind
        super().__init__(message)


class NiyanCompatibilityError(NiyanFileSystemError):
    """Report an incompatible server API or filesystem protocol."""


class NiyanIntegrityError(OSError, NiyanFileSystemError):
    """Report complete-content size or digest verification failure."""


class NiyanTransferError(OSError, NiyanFileSystemError):
    """Report a retry-exhausted data-transfer failure."""
