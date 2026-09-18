# Niyān Python Client

This package provides the `niyan` command and the importable Python client for Niyān.

The CLI is the supported interface for authenticating to a Niyān installation and coordinating dataset repository workflows. The filesystem module will provide read-only, `fsspec`-compatible streaming and downloading without invoking CLI commands or requiring Git.

Repository commands require Git on `PATH`, and commands that materialize large-file content also require Git LFS. Niyān invokes both internally rather than asking users to manage dataset repositories with their CLIs. Dataset clones use depth-one, single-branch Git history by default; `niyan fetch` leaves the worktree and LFS cache unchanged, while `niyan pull` permits only fast-forward updates and then materializes the saved `--include`/`--exclude` selection. `--metadata-only` leaves LFS paths as pointer files for that invocation, and `--full-history` explicitly converts a shallow checkout to complete history.

`niyan add` delegates pathspec and ignore handling to Git, stores new binary files and files larger than 10 MiB through stock Git LFS, and keeps small text in ordinary Git. `--lfs` and `--git` persist explicit standard `.gitattributes` overrides; an existing tracked file keeps its current storage mode unless the user deliberately overrides it.

Niyān-managed checkouts configure the upload-only `niyan-multipart` Git LFS custom transfer automatically. The agent uses the checkout's selected access token for bounded multipart-control requests, verifies the local LFS object, uploads byte ranges directly to signed object-storage URLs with four-part concurrency and bounded retries, and leaves downloads on Git LFS's standard basic adapter.

The initial development commands are:

```shell
uv sync
uv run python -m unittest discover -s tests
uv run niyan --help
```

Access-token secrets use the operating-system credential store through `keyring`. Headless environments without a compatible backend may opt into the explicitly insecure mode-`0600` file store with `--insecure-storage`.
