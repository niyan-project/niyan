# Niyān Python Client

[![Python client coverage](https://codecov.io/github/niyan-project/niyan/graph/badge.svg?flag=python-client)](https://app.codecov.io/github/niyan-project/niyan)
[![Python 3.11–3.13](https://img.shields.io/badge/Python-3.11%E2%80%933.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](../../LICENSE)

This package provides the `niyan` command and the importable Python client for Niyān.

Install the command in an isolated environment with:

```shell
pipx install niyan
```

Install the importable client in a Python project with `pip install niyan` or the project's normal package manager. Python 3.11 through 3.13 are supported. The CLI's repository commands also require Git, and materializing large-file content requires Git LFS.

The CLI is the recommended interface for authenticating to a Niyān installation and coordinating dataset repository workflows. The `niyan.filesystem` module provides a registered, read-only `fsspec` filesystem without invoking CLI commands or requiring Git. Canonical URLs such as `niyan://data.example.edu/lab/images/path/to/file?revision=main` support metadata operations plus binary `open`, `read`, `seek`, `tell`, `cat_file`, `get_file`, and recursive `get` access. Git LFS content uses bounded ranges directly against authorized object-storage URLs; ordinary Git blobs use the authenticated repository API. Full downloads stream to a sibling `.niyan-part` artifact and atomically publish the final file only after size and available Git LFS SHA-256 verification succeed.

Niyān configures and delegates to Git and Git LFS; standard Git and Git LFS commands are also supported for users who prefer them. Dataset clones made through Niyān use depth-one, single-branch Git history by default; `niyan fetch` leaves the worktree and LFS cache unchanged, while `niyan pull` permits only fast-forward updates and then materializes the saved `--include`/`--exclude` selection. `--metadata-only` leaves LFS paths as pointer files for that invocation, and `--full-history` explicitly converts a shallow checkout to complete history.

`niyan add` delegates pathspec and ignore handling to Git, stores new binary files and files larger than 10 MiB through stock Git LFS, and keeps small text in ordinary Git. `--lfs` and `--git` persist explicit standard `.gitattributes` overrides; an existing tracked file keeps its current storage mode unless the user deliberately overrides it.

Niyān-managed checkouts configure the upload-only `niyan-multipart` Git LFS custom transfer automatically. The agent uses the checkout's selected access token for bounded multipart-control requests, verifies the local LFS object, uploads byte ranges directly to signed object-storage URLs with four-part concurrency and bounded retries, and leaves downloads on Git LFS's standard basic adapter.

`niyan cache status` reports the checkout's local Git LFS storage and the content protected or reclaimable under Git LFS's standard retention policy. `niyan cache prune --dry-run` previews remotely verified candidates without mutation. An actual prune repeats remote verification and requires explicit confirmation before Git LFS deletes anything.

The initial development commands are:

```shell
uv sync
uv run python -m unittest discover -s tests
uv run niyan --help
```

Access-token secrets use the operating-system credential store through `keyring`. Headless environments without a compatible backend may opt into the explicitly insecure mode-`0600` file store with `--insecure-storage`.
