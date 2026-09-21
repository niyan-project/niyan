# Python Filesystem Client

- **Status:** Accepted
- **Audience:** maintainers, client implementers, and research-computing users
- **Last reviewed:** 2026-09-18

## Compatibility Boundary

The URI grammar, host and credential selection, capability negotiation, public error categories, typing policy, and synchronous v1 surface in this document are accepted. New public operations or changes to these contracts require a specification change; implementation details such as the HTTP client, internal coroutine use, bounded block-cache strategy, and retry machinery may evolve without changing the public filesystem behavior.

## Purpose

The Niyān Python client gives Python programs read-only access to versioned dataset files through the standard `fsspec` ecosystem. It is an importable part of the unified `niyan` PyPI distribution, which also provides the `niyan` console entry point. The filesystem client is not a wrapper around the CLI.

Its primary users are researchers working with files too large to clone or copy casually, including users who run analysis on shared clusters and HPC systems. They must be able either to stream only the byte ranges their libraries need or to download complete files onto high-throughput local or scratch storage.

## V1 Requirements

The package must:

- implement a read-only `fsspec` filesystem registered under a stable Niyān protocol;
- authenticate to the Niyān REST API without requiring raw S3 credentials;
- address a dataset, Git revision, and path, and report the exact resolved commit;
- list directories and inspect file metadata needed by normal `fsspec` consumers;
- open files in binary read mode without first downloading the complete file;
- support seeking and ranged reads for LFS-backed objects when the storage service supports HTTP ranges;
- download individual files and recursive directory selections incrementally;
- keep every recursive operation pinned to one resolved commit;
- refresh expired signed transfer authorization transparently when safe to retry;
- avoid holding an entire large object in memory or requiring equivalent temporary disk space for streaming reads;
- work without Git, Git LFS, invoking the Niyān CLI, or a local repository checkout; and
- interoperate through normal `fsspec` entry points so downstream libraries do not need Niyān-specific integrations.

The package should have a small dependency footprint and support headless, non-interactive execution suitable for schedulers and compute nodes.

## Filesystem Surface

The minimum v1 surface should cover the `fsspec` equivalents of:

- `ls`, `info`, `exists`, `isfile`, and `isdir`;
- `open(path, "rb")` with `read`, `seek`, `tell`, and context-manager behavior;
- `cat_file` with byte-range support; and
- `get_file` plus recursive download of a selected directory.

Write, append, delete, rename, copy-to-remote, commit, and branch mutation operations are outside the v1 Python client. Those remain Niyān CLI or web workflows; the CLI may delegate repository mechanics to Git and Git LFS internally.

The package may implement additional `fsspec` methods when they follow naturally from the same API, but must not grow a second dataset-management SDK under the guise of filesystem compatibility.

## Addressing and Version Consistency

The canonical URL form is:

```text
niyan://<installation-authority>/<group-path>/<dataset>/<repository-path>?revision=<git-revision>
```

For example:

```text
niyan://data.example.edu/labs/microscopy/images/cell-001.tif?revision=main
```

The authority identifies the Niyān installation and may include a port. HTTPS is implied for non-loopback authorities. `localhost`, `127.0.0.1`, and `[::1]` may use HTTP for development. V1 does not support an installation mounted beneath a URL path prefix.

The path begins with a human-facing dataset path, including any nested groups, followed by an optional repository path. The server resolves the only path prefix that names a dataset and returns its immutable dataset UUID together with the remaining repository path. Niyān's shared group-and-dataset path constraint makes this boundary unambiguous: a path component beneath one parent cannot simultaneously name both a group and a dataset. The dataset root may be written with or without a trailing slash.

URI path components and the `revision` value use standard percent encoding. Credentials must never appear in the authority, path, query, or fragment. Unknown query parameters are rejected instead of silently changing cache identity or read behavior.

The `revision` query parameter accepts a branch, tag, or full commit identifier. When it is omitted, the server's default branch is used. A URL revision and an explicit `revision=` storage option may both be supplied only when their decoded values agree. The same agreement rule applies when an explicit `host=` option accompanies a URL authority.

Direct construction may supply `host`, `dataset`, and `revision` separately for applications that maintain connection options independently from paths. When no URL authority or explicit host is available, host selection uses `NIYAN_HOST` and then the user's configured default host. The canonical URL remains the portable representation used by ordinary `fsspec.open()` calls.

An explicit commit is already immutable. When the caller supplies a branch or tag, the client must resolve it to a commit before accessing file content. A file handle remains bound to that commit for its lifetime. A recursive listing or download resolves once and uses the same commit for every child operation.

This guarantee prevents a branch update from silently combining files from different dataset versions during one logical operation.

## Transfer Model

The client asks the Niyān API for authorized metadata and transfer actions. For an LFS object, the data path is directly between the Python process and S3-compatible storage; Django does not proxy the bytes.

An open file uses bounded HTTP range requests to satisfy reads and seeks where the object store supports them. Sequential access should prefetch bounded blocks rather than issue a request for every small read, but cache and block-size defaults remain to be benchmarked.

Signed transfer URLs are short-lived bearer credentials. The client must not log them, persist them in filesystem metadata, or expose them as the normal public return value. If a URL expires, the client may request a replacement and retry only when doing so preserves the caller's read position and does not misrepresent a failed operation as successful.

Dataset deletion immediately prevents Niyān from issuing or renewing transfer authorization. A direct object-storage response that was already opened may finish because its bytes no longer pass through Django; deletion cannot retroactively revoke bytes already in flight. A later range request may continue only while its previously issued action remains accepted by storage. If storage rejects that action, the client must reauthorize, surface the resulting not-found response, and never reinterpret the failed read as end-of-file or complete content.

Files stored as ordinary Git blobs must remain readable through the same filesystem API. Their internal transport may differ, but the client-facing path, revision, metadata, and error semantics should not.

## Downloads and Integrity

Full-file downloads must stream into a temporary or explicitly partial local destination and publish the final path only after success. An interrupted transfer must leave a recognizable partial artifact or clean it up according to an explicit option; it must not leave a truncated file under the requested final name.

An object recorded as available but missing from storage is a transfer failure, not an empty file. The client must not publish the final path, and the retained partial artifact follows the same explicit cleanup policy as any other interrupted download.

When an expected Git LFS SHA-256 object identifier is available, a complete download should verify it incrementally before finalizing the file. Partial and ranged reads cannot claim whole-object verification. Size and resolved revision metadata must remain available to callers that need their own checks.

`get_file` and recursive `get` write a sibling `<destination>.niyan-part` file and use an atomic local replace only after the expected size and, for Git LFS content, SHA-256 identifier have been verified. Existing final files are replaced by default only after that verification. `overwrite=False` fails before transfer and rechecks immediately before publication. Failed transfers retain the recognizable partial artifact by default for diagnosis; `keep_partial=False` removes it. V1 does not resume a retained partial artifact.

Parent directories are created as needed. New files use the process and filesystem's ordinary creation permissions and umask; Niyān does not preserve repository modification times or executable bits on downloaded dataset content. Recursive downloads are serial in v1 and place the selected directory's contents beneath the caller's destination. The entire traversal and every child authorization remain pinned to the single commit resolved at the start. Parallel recursive transfer and resumable partial artifacts may be added later without weakening these publication and integrity guarantees.

## Authentication

Headless token authentication is required for v1 because cluster and scheduled jobs may have no browser. Credential selection uses this order:

1. an explicit `token=` filesystem or storage option;
2. the process-local `NIYAN_TOKEN` environment variable; and
3. the package's shared stored-credential selection for the resolved installation and dataset.

Stored selection uses the CLI contract: checkout-local exact-dataset binding, user-configured exact-dataset binding, checkout-local user-level binding, then user-configured user-level binding. A checkout-local binding is considered only when the process working directory is within that checkout. Once a binding is selected, a missing, expired, revoked, or insufficient token is reported without silently falling back to a broader credential.

The client must never invoke a CLI command to discover credentials or copy secrets into dataset URLs, source code, non-secret configuration, exceptions, logs, representations, or deterministic filesystem cache identities. Importing `niyan.filesystem` and resolving its registered class perform no Git, checkout, configuration, keyring, or network discovery. Credential lookup is lazy and occurs only when an authorized filesystem operation requires it.

Browser-assisted login is not part of the filesystem API. A person may establish stored credentials separately with `niyan auth login`; schedulers and headless jobs normally use `NIYAN_TOKEN` or an explicit token supplied by their secret-management environment.

## Capability and Version Negotiation

The server exposes an inexpensive `/api/v1/capabilities` operation containing the supported REST API versions, filesystem protocol version, and stable feature identifiers. The v1 client requires filesystem protocol version `1` and the features needed for the requested operation. At minimum, the completed Phase 4 server advertises exact revision resolution, repository metadata, Git-blob reads, and authorized Git LFS download actions.

The Phase 4 REST contract uses these operations:

- `GET /api/v1/capabilities` advertises `api_versions`, filesystem `protocol_version`, and stable kebab-case feature identifiers without requiring authentication;
- `GET /api/v1/datasets/resolve?path=...` resolves the longest visible dataset prefix and returns its immutable ID, canonical dataset path, remaining `repository_path`, and Git URL;
- `GET /api/v1/datasets/{dataset_id}/repository/revisions/resolve?revision=...` pins a branch, tag, or commit expression to `resolved_commit`;
- the existing tree and blob metadata operations accept that exact commit in their `revision` parameter; and
- `GET /api/v1/datasets/{dataset_id}/repository/download?revision=...&path=...` returns an authorized action with exact commit, normalized path, Git blob identity, optional LFS SHA-256 identity, size, storage kind, expiry, headers, and object-specific range support.

Dataset and path resolution must preserve private-resource non-disclosure. A download action is a short-lived capability, not stable metadata, and must not be cached beyond the operation that requested it.

Object-specific behavior remains attached to the resolved metadata or transfer action. In particular, HTTP range support and action expiry are properties of a particular file transfer rather than installation-wide promises.

The client may cache installation capabilities for the lifetime of a filesystem instance. It must fail with a compatibility error before transferring content when the server cannot provide the required semantics. An unknown additive feature is ignored; an unsupported protocol version or missing required feature is never silently emulated with weaker consistency.

The REST path version, filesystem protocol version, and Python package version are separate compatibility dimensions. The `0.x` package series may evolve before the v1 release, but every published change still requires release notes and tests. Beginning with `1.0.0`, the documented Python surface follows semantic versioning. Additive REST v1 and filesystem-protocol features do not require a new protocol version; a change that breaks accepted semantics does.

## Public Errors

The filesystem package exposes stable exception categories rooted at `NiyanFileSystemError`:

- `NiyanAuthenticationError`, also a `PermissionError`, for a missing, invalid, expired, or revoked credential;
- `NiyanPermissionError`, also a `PermissionError`, for an authenticated identity that lacks access;
- `NiyanNotFoundError`, also a `FileNotFoundError`, for a missing installation resource, dataset, revision, or path, with a non-secret resource kind available to callers;
- `NiyanCompatibilityError` for an unsupported server API or missing required capability;
- `NiyanIntegrityError`, also an `OSError`, for size or digest verification failure; and
- `NiyanTransferError`, also an `OSError`, for retry-exhausted transport or object-storage failure.

Expired signed actions are refreshed internally when retrying is safe. They become `NiyanTransferError` only when refresh fails or the operation cannot preserve its position and semantics. Timeouts retain their original exception as the cause. Error text and structured attributes must never contain access tokens or signed URLs.

Common filesystem failures deliberately inherit from Python's expected built-in categories so ordinary `fsspec` consumers can handle `FileNotFoundError`, `PermissionError`, and `OSError` without importing Niyān-specific classes.

## Compatibility and Packaging

The client and server need an explicit compatibility contract for REST API versions and filesystem capabilities. Unsupported server features must fail clearly rather than silently changing read semantics.

The filesystem client and CLI are versioned and released together as the `niyan` PyPI distribution for Python 3.11 or newer. Installing the distribution provides both the importable library and the `niyan` console entry point. Filesystem imports must not eagerly import CLI-only modules or perform Git, credential-store, configuration, or network discovery.

The synchronous `fsspec` interface is the supported v1 public contract. This covers normal blocking `open`, `read`, `seek`, listing, and download calls used by pandas, Polars, PyArrow, xarray, Dask, PyTorch datasets, and similar consumers. The implementation may use `fsspec` asynchronous primitives and non-blocking HTTP internally, but direct coroutine methods are not a supported v1 surface. Adding a public asynchronous contract later must not weaken or replace the synchronous one.

Public modules, classes, methods, arguments, return values, and exception attributes include inline type annotations. The distribution includes `niyan/py.typed` as the PEP 561 marker so type checkers consume those annotations from the installed wheel. The marker is a packaging declaration, not a substitute for maintaining accurate annotations.

`fsspec` is a required base dependency rather than an optional extra because protocol registration and compatibility are the package's core Python-client purpose. The remaining base dependency set must remain suitable for headless HPC environments; CLI-only dependencies and imports must not make ordinary filesystem use depend on an available desktop session or credential-store backend.

## Non-goals

- Uploading or mutating datasets.
- Creating commits, branches, tags, or releases.
- Cloning Git repositories or materializing Git submodules.
- Providing dataframe, SQL, query-planning, distributed-compute, or domain-specific analysis APIs.
- Replacing the ordinary local filesystem after a user explicitly downloads a file.
- Exposing permanent S3 credentials or relying on users being granted direct bucket access.

## Acceptance Scenarios

Before v1 is considered complete, automated tests should demonstrate that:

1. A private dataset can be listed with an authorized token and cannot be listed with a denied token.
2. A caller can open a multi-gigabyte LFS file and read small noncontiguous ranges without downloading the complete object.
3. pandas, Polars, PyArrow, or another representative `fsspec` consumer can read through the normal filesystem interface without a Niyān-specific adapter.
4. A recursive download from a moving branch uses one resolved commit even if the branch changes during transfer.
5. A signed URL that expires during a safe read can be refreshed without restarting the logical operation from byte zero.
6. A failed full download never appears at the requested final path as if it were complete.
7. A full LFS download detects an object whose bytes do not match its expected identifier.
8. The filesystem client operates in a clean Python environment with no Git or Git LFS installed and never invokes the `niyan` console entry point.
