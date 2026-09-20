# Coverage policy

Niyān uses separate Codecov reports for the Django server and the unified Python client. Coverage is a regression signal and a way to find missing behavioral tests; it is not a substitute for assertions, realistic boundaries, or review.

## Baseline and targets

The Phase 8 baseline, measured by Codecov on `main`, is:

| Component | Baseline | Initial enforced target | Longer-term direction |
| --- | ---: | ---: | ---: |
| Django server | 77.36% | 85% | Ratchet toward 90% or higher |
| Python client | 69.37% | 80% | Ratchet toward 90% or higher |

CI enforces the component targets and 90% patch coverage for each flag. The targets should be raised in reviewable increments as meaningful tests land; they must not be lowered merely to make a change pass.

## Risk priorities

Coverage work should prioritize behavior where a defect could disclose data, publish invalid repository state, corrupt or truncate a transfer, strand a checkout, or make recovery unsafe.

The initial server audit identified the largest important gaps in browser commits, repository creation and browsing, Git hook entry points, Git LFS negotiation and verification, object cleanup, authorization failures, and configuration used by hook subprocesses.

The initial client audit identified the largest important gaps in HTTP error handling, CLI dispatch, credential-store failures, project dependency handling, fsspec metadata and ranged reads, Git and Git LFS delegation, multipart interruption, cache safety, and atomic download recovery.

Tests for these areas should assert externally meaningful results and failure behavior. Server tests always run against PostgreSQL, including local development through the ephemeral Docker Compose test service; SQLite is not an accepted substitute. Prefer real Git, Git LFS, PostgreSQL, and HTTP boundaries or faithful storage doubles where their semantics are the subject of the test. A test that merely imports a module or executes a line without validating behavior does not satisfy this policy.

## Exclusions

Coverage currently excludes tests, generated Django migrations, `manage.py`, and ASGI or WSGI process bootstraps. New exclusions require a documented reason in the same change and must describe why the code cannot be exercised meaningfully. Difficult subprocess, error, or platform branches are not sufficient reasons to exclude code.

Coverage must never be increased by deleting scenarios, weakening assertions, replacing integration tests with less representative mocks, or marking reachable code as excluded. Partial branches remain visible in Codecov.

## CI behavior

The `server` and `python-client` flags are uploaded independently on every applicable CI run. Missing or failed uploads fail CI. The component jobs enforce the 85% and 80% project floors directly, while Codecov enforces the configured project statuses and 90% patch statuses.

Pull requests must pass `codecov/project/server`, `codecov/project/python-client`, `codecov/patch/server`, and `codecov/patch/python-client` in addition to the component test jobs.

When a legitimate behavior change lowers coverage, the preferred response is to add the missing test in the same change. A temporary exception must identify an owner, explain the concrete blocker, and state when the exception will be removed.
