# Python Filesystem Client

- **Status:** Draft
- **Audience:** maintainers, client implementers, and research-computing users
- **Last reviewed:** not yet reviewed

## Purpose

The Niyān Python client gives Python programs read-only access to versioned dataset files through the standard `fsspec` ecosystem. It is a proper PyPI package, not a Python wrapper around the `niyan` CLI.

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
- work without Git, Git LFS, the Niyān CLI, or a local repository checkout; and
- interoperate through normal `fsspec` entry points so downstream libraries do not need Niyān-specific integrations.

The package should have a small dependency footprint and support headless, non-interactive execution suitable for schedulers and compute nodes.

## Filesystem Surface

The minimum v1 surface should cover the `fsspec` equivalents of:

- `ls`, `info`, `exists`, `isfile`, and `isdir`;
- `open(path, "rb")` with `read`, `seek`, `tell`, and context-manager behavior;
- `cat_file` with byte-range support; and
- `get_file` plus recursive download of a selected directory.

Write, append, delete, rename, copy-to-remote, commit, and branch mutation operations are outside the v1 Python client. Those remain Git, Git LFS, CLI, or web workflows.

The package may implement additional `fsspec` methods when they follow naturally from the same API, but must not grow a second dataset-management SDK under the guise of filesystem compatibility.

## Addressing and Version Consistency

A filesystem URL needs to identify:

1. the Niyān installation;
2. the dataset namespace and name;
3. a commit, tag, or branch; and
4. a path within the dataset.

The exact URI grammar is not yet accepted. It must be unambiguous, safely encode path components, and allow connection options or configuration to select an installation without embedding credentials in URLs.

An explicit commit is already immutable. When the caller supplies a branch or tag, the client must resolve it to a commit before accessing file content. A file handle remains bound to that commit for its lifetime. A recursive listing or download resolves once and uses the same commit for every child operation.

This guarantee prevents a branch update from silently combining files from different dataset versions during one logical operation.

## Transfer Model

The client asks the Niyān API for authorized metadata and transfer actions. For an LFS object, the data path is directly between the Python process and S3-compatible storage; Django does not proxy the bytes.

An open file uses bounded HTTP range requests to satisfy reads and seeks where the object store supports them. Sequential access should prefetch bounded blocks rather than issue a request for every small read, but cache and block-size defaults remain to be benchmarked.

Signed transfer URLs are short-lived bearer credentials. The client must not log them, persist them in filesystem metadata, or expose them as the normal public return value. If a URL expires, the client may request a replacement and retry only when doing so preserves the caller's read position and does not misrepresent a failed operation as successful.

Files stored as ordinary Git blobs must remain readable through the same filesystem API. Their internal transport may differ, but the client-facing path, revision, metadata, and error semantics should not.

## Downloads and Integrity

Full-file downloads must stream into a temporary or explicitly partial local destination and publish the final path only after success. An interrupted transfer must leave a recognizable partial artifact or clean it up according to an explicit option; it must not leave a truncated file under the requested final name.

When an expected Git LFS SHA-256 object identifier is available, a complete download should verify it incrementally before finalizing the file. Partial and ranged reads cannot claim whole-object verification. Size and resolved revision metadata must remain available to callers that need their own checks.

Resume behavior, overwrite policy, local permissions, preservation of modification times, and parallel recursive-download limits require separate decisions and tests against real HPC filesystems.

## Authentication

Headless token authentication is required for v1 because cluster and scheduled jobs may have no browser. The package must also be able to reuse a supported user credential established on a login node without copying secrets into dataset URLs or source code.

Credential discovery order, configuration-file location, environment-variable names, browser-assisted login integration, and secure storage behavior remain to be specified. Error messages must distinguish authentication failure, denied dataset access, missing paths, expired transfer authorization, and unavailable object storage.

## Compatibility and Packaging

The client and server need an explicit compatibility contract for REST API versions and filesystem capabilities. Unsupported server features must fail clearly rather than silently changing read semantics.

The package is versioned and released independently from the standalone CLI. Its PyPI distribution name, Python version range, optional extras, type-hint policy, sync/async support, and semantic-versioning commitment remain open questions.

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
8. The package operates in a clean Python environment with no Git, Git LFS, or `niyan` executable installed.
