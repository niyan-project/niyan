# ADR 0009: Distribute the CLI and Filesystem Client as One Python Package

- **Status:** Accepted
- **Date:** 2026-09-17

## Context

The Niyān CLI and read-only `fsspec` client were initially planned as independently distributed products. Both are now intended to be implemented in Python, and both need the same server API negotiation, HTTP transport, authentication, host configuration, dataset and revision resolution, error model, and large-file download primitives.

Maintaining separate distributions would require either duplicating those behaviors or introducing another shared package and coordinating three releases. That separation would add work and opportunities for inconsistent security and transfer behavior before either client has established an independent release cadence.

The products still have different runtime requirements. Repository-oriented CLI commands may invoke Git and Git LFS and use interactive credential storage. Filesystem consumers must remain suitable for notebooks, schedulers, and headless HPC nodes, and must not require Git, a checkout, a desktop session, or execution of a CLI command.

## Decision

Publish one Python 3.11-or-newer distribution named `niyan`. It provides:

- the `niyan` console entry point for CLI workflows; and
- an importable, read-only `fsspec` filesystem implementation.

The distribution is versioned and released as one product. `pipx install niyan` is the standard isolated PyPI installation for CLI users. Installing `niyan` into a Python project through pip or uv provides the library and an environment-local console entry point. The project will also provide the specified bootstrap installer and checksummed manual artifacts from the same source and version.

The CLI and filesystem implementation remain separate internal modules. They may depend on narrowly scoped shared modules for HTTP, authentication, configuration, models, errors, and transfer behavior. Filesystem code must not import CLI user-interface modules, invoke the console entry point, shell out to Git or Git LFS, or require a repository checkout. Importing the package must not itself inspect Git state, access a credential store, or perform network I/O.

The unified package initially lives under `clients/python/`. The existing prototype under `clients/cli/` will move only in a separately reviewed implementation stage.

This decision supersedes the independent CLI and Python-client distribution requirement in [ADR 0001](0001-modular-monorepo.md). It does not weaken the boundary between client code and Django server internals.

## Consequences

- Authentication, API compatibility, dataset resolution, errors, and transfer logic can have one implementation and test suite.
- Users install one named package whether they need the CLI, the filesystem API, or both.
- CLI and filesystem changes share one version and release cadence.
- The base dependency set must remain acceptable on headless HPC systems, and CLI-only imports must be lazy enough not to disturb library use.
- Installing the Python library also installs a console entry point in that environment.
- A future split remains possible if dependency weight, governance, or genuinely different release cadences outweigh the coordination benefit.
