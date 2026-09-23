# Niyān Python Client

[![Python client coverage](https://codecov.io/github/niyan-project/niyan/graph/badge.svg?flag=python-client)](https://app.codecov.io/github/niyan-project/niyan)
[![PyPI](https://img.shields.io/pypi/v/niyan)](https://pypi.org/project/niyan/)
[![Python versions](https://img.shields.io/pypi/pyversions/niyan)](https://pypi.org/project/niyan/)
[![License](https://img.shields.io/pypi/l/niyan)](https://pypi.org/project/niyan/)

This package provides the `niyan` command and the importable Python client for Niyān.

Install the command in an isolated environment with:

```shell
pipx install niyan
```

Install the importable client in a Python project with `pip install niyan` or the project's normal package manager. Python 3.11 through 3.13 are supported. The CLI's repository commands also require Git, and materializing large-file content requires Git LFS.

The CLI is the recommended interface for authenticating to a Niyān installation and coordinating dataset repository workflows. The `niyan.filesystem` module provides a registered, read-only `fsspec` filesystem without invoking CLI commands or requiring Git. Canonical URLs such as `niyan://data.example.edu/lab/images/path/to/file?revision=main` support metadata operations plus binary `open`, `read`, `seek`, `tell`, `cat_file`, `get_file`, and recursive `get` access. Git LFS content uses bounded ranges directly against authorized object-storage URLs; ordinary Git blobs use the authenticated repository API. Full downloads stream to a sibling `.niyan-part` artifact and atomically publish the final file only after size and available Git LFS SHA-256 verification succeed.

Niyān configures and delegates to Git and Git LFS; standard Git and Git LFS commands are also supported for users who prefer them. Dataset clones made through Niyān use depth-one, single-branch Git history by default; `niyan fetch` leaves the worktree and LFS cache unchanged, while `niyan pull` permits only fast-forward updates and then materializes the saved `--include`/`--exclude` selection. `--metadata-only` leaves LFS paths as pointer files for that invocation, and `--full-history` explicitly converts a shallow checkout to complete history.

`niyan add` delegates pathspec, ignore handling, and storage selection to Git and the repository's `.gitattributes`. Configure Git LFS for the dataset's formats or directories before staging a large tree. `--lfs` and `--git` persist explicit standard `.gitattributes` overrides when a one-off selection should also become repository policy. Add `--verbose` to print each path as Git stages it, which is especially helpful for datasets containing many files.

Niyān-managed checkouts configure the upload-only `niyan-multipart` Git LFS custom transfer automatically. Git LFS runs up to its standard `lfs.concurrenttransfers` setting—eight object transfers by default—while each large multipart object uses up to four part workers. The agent uses the checkout's selected access token for bounded multipart-control requests, verifies each local LFS object, uploads byte ranges directly to signed object-storage URLs with bounded retries, and leaves downloads on Git LFS's standard basic adapter. `niyan push` shows Git and Git LFS progress by default; use `--quiet` to suppress it.

`niyan cache status` reports the checkout's local Git LFS storage and the content protected or reclaimable under Git LFS's standard retention policy. `niyan cache prune --dry-run` previews remotely verified candidates without mutation. An actual prune repeats remote verification and requires explicit confirmation before Git LFS deletes anything.

The initial development commands are:

```shell
uv sync
uv run python -m unittest discover -s tests
uv run niyan --help
```

Access-token secrets use the operating-system credential store through `keyring`. Headless environments without a compatible backend may opt into the explicitly insecure mode-`0600` file store with `--insecure-storage`.
