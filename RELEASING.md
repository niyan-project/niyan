# Releasing Niyān

Niyān uses one Semantic Version for the server image, CLI, and Python filesystem client. The version in `apps/server/pyproject.toml`, `clients/python/pyproject.toml`, and `clients/python/src/niyan/__init__.py` must match.

## Prepare a release

1. Choose the next version from the public compatibility impact.
2. Move relevant `CHANGELOG.md` entries from `Unreleased` into a `MAJOR.MINOR.PATCH` section dated in ISO format.
3. Update all three version declarations and refresh both uv lockfiles.
4. Run the complete CI suite and build the production image locally.
5. Merge the focused release-preparation commit to `main`.
6. Create and push an immutable `vMAJOR.MINOR.PATCH` tag at that exact commit. Never use abbreviated tags such as `v0.2`.
7. After the container workflow succeeds, publish the GitHub release from the same existing tag.

Pushing the tag triggers `.github/workflows/publish-container.yml`. The workflow rejects non-semantic or mismatched tags, builds and smoke-tests the exact source, publishes `linux/amd64` and `linux/arm64` images, and adds version, major/minor, and major tags beneath `ghcr.io/niyan-project/niyan`. It also publishes a GitHub provenance attestation for the immutable image digest. The workflow authenticates with its short-lived `GITHUB_TOKEN`; no registry token is stored.

After the first successful publication, an organization owner must set the `niyan` container package visibility to public and confirm that an unauthenticated `docker pull ghcr.io/niyan-project/niyan:MAJOR.MINOR.PATCH` succeeds. This is a one-time package setting, not a repository secret.

Publishing the GitHub release triggers `.github/workflows/publish-python.yml`. The workflow verifies the same tag, package metadata, tests, import boundaries, and reproducibility of the wheel and source distribution. Its `publish` job then enters the protected `pypi` environment and exchanges GitHub's OIDC identity for a short-lived PyPI credential. The official PyPI publishing action creates and uploads trusted-publishing attestations for the distributions by default. No long-lived PyPI token or TestPyPI environment is used.

The PyPI trusted publisher is bound to repository `niyan-project/niyan`, workflow `publish-python.yml`, and environment `pypi`. Publishing a GitHub release is the deliberate human action that starts the workflow, and the environment accepts only version tags matching `v*`. Enable required-reviewer protection when the repository plan supports it, and never add publishing credentials to repository secrets.

If either publication fails, do not move or reuse the tag for different source. Correct the problem, increment the version, and create a new release. PyPI versions, GHCR version tags, attestations, and GitHub release assets are immutable release records.
