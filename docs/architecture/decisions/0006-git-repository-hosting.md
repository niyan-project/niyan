# ADR 0006: Store Bare Repositories on a Persistent Filesystem and Serve Them with Git

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

Niyān needs authenticated clone, fetch, and push behavior without reimplementing the Git wire protocol. Git repository state consists of refs, objects, configuration, and hooks that Git already knows how to manage correctly. The control plane needs to authorize access and apply policy while leaving repository semantics to Git.

## Decision

Store each dataset as a bare Git repository beneath a configurable persistent repository root. Address repository directories by immutable dataset UUID rather than namespace or dataset name so renames do not require filesystem moves and user-controlled names never become filesystem paths.

Serve repositories over smart HTTP using Git's `git-http-backend`. Django authenticates and authorizes the request before handing it to a small adapter that supplies the required CGI environment and streams the request and response. Niyān does not implement pack negotiation, object traversal, or the Git wire protocol.

The repository filesystem is a separate durable data store from PostgreSQL and S3-compatible LFS storage. Development may use a local directory; deployment may mount a persistent volume or other filesystem with the required Git semantics.

## Consequences

- Standard Git clients can clone, fetch, and push without a Niyān-specific transport.
- Git remains authoritative for refs, objects, and history.
- Dataset renames and namespace moves do not change repository storage paths.
- Repository storage requires its own capacity planning, backup, restore, permissions, and garbage-collection procedures.
- Horizontal scaling requires a shared filesystem, repository affinity, or a later repository-storage design; v1 does not pretend a local path is automatically multi-node safe.
- The adapter must preserve streaming behavior, protocol headers, authenticated actor identity, and receive-pack hook results without buffering complete packfiles in Django memory.

