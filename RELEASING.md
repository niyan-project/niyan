# Releasing Niyān

Niyān uses Semantic Versioning for the unified Python package. The version in `clients/python/pyproject.toml` and `clients/python/src/niyan/__init__.py` must match. CLI and Python filesystem behavior ship together under that version.

## Prepare a release

1. Choose the next version from the public compatibility impact.
2. Move relevant `CHANGELOG.md` entries from `Unreleased` into a `MAJOR.MINOR.PATCH` section dated in ISO format.
3. Update both package version declarations and run the complete CI suite.
4. Merge the focused release-preparation commit to `main`.
5. Create a GitHub release from that exact commit using tag `vMAJOR.MINOR.PATCH`.

Publishing the GitHub release triggers `.github/workflows/publish-python.yml`. The workflow verifies the tag, package metadata, tests, import boundaries, and reproducibility of the wheel and source distribution. Its `publish` job then enters the protected `pypi` environment and exchanges GitHub's OIDC identity for a short-lived PyPI credential. The official PyPI publishing action creates and uploads trusted-publishing attestations for the distributions by default. No long-lived PyPI token or TestPyPI environment is used.

The PyPI trusted publisher is bound to repository `niyan-project/niyan`, workflow `publish-python.yml`, and environment `pypi`. Publishing a GitHub release is the deliberate human action that starts the workflow, and the environment accepts only version tags matching `v*`. Enable required-reviewer protection when the repository plan supports it, and never add publishing credentials to repository secrets.

If publishing fails, do not move or reuse the tag for different source. Correct the problem, increment the version, and create a new release. Package versions and GitHub release assets are immutable once published.
