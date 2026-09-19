class NiyanCliError(RuntimeError):
    """Base error whose message is safe to show to a CLI user."""


class ConfigurationError(NiyanCliError):
    """Report invalid or unavailable CLI configuration."""


class CredentialError(NiyanCliError):
    """Report secure-storage or credential-selection failure."""


class ApiError(NiyanCliError):
    """Report a sanitized error returned by the Niyān API."""

    def __init__(self, message, *, status=None, code=None):
        """Record public HTTP status and error-code metadata.

        Parameters
        ----------
        message : str
            Actionable public error detail.
        status : int, optional
            HTTP response status.
        code : str, optional
            Stable Niyān API error code.
        """

        self.status = status
        self.code = code
        super().__init__(message)


class GitError(NiyanCliError):
    """Report failure to invoke or complete an internal Git operation."""


class GitConflictError(GitError):
    """Report a safe refusal caused by divergent or conflicting Git state."""


class GitDependencyError(GitError):
    """Report a missing local Git or Git LFS dependency."""
