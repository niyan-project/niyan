# ADR 0005: Transfer Large Objects Directly Rather Than Through Django

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

Dataset files may range from kilobytes to terabytes. Routing those bytes through Django workers would create avoidable application bandwidth, memory, timeout, and scaling pressure while duplicating capabilities already provided by S3-compatible services.

## Decision

After authenticating and authorizing a Git LFS, browser, or Python filesystem request, the control plane issues a short-lived, object- and operation-scoped transfer action. The client sends or receives the bytes directly to or from S3-compatible object storage.

Django handles policy, orchestration, and verification metadata but does not proxy the bulk content. A verification step may be required before a newly uploaded object becomes eligible for an accepted ref update.

## Consequences

- Application servers remain out of the large-file data path.
- Object storage can provide multipart transfer, range requests, and its native horizontal scale.
- Cross-origin policy, URL expiry, retry behavior, checksums, incomplete multipart uploads, and verification need careful protocol specifications and tests.
- Signed URLs are bearer credentials and must not be logged or granted before authorization.
- Client-side encryption where the server cannot interpret content is not included by this decision and would require separate design work.
