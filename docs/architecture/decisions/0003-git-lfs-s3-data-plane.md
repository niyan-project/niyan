# ADR 0003: Use Git LFS with an S3-compatible Data Plane

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

Datasets may contain files too large for ordinary Git blob storage. The project needs content-addressed large objects, local checkout integration, resumable transfer capabilities, and an established pointer format without adopting DVC's broader product model.

## Decision

Use standard Git LFS pointer objects and protocol for generally all binary files. Store LFS object bytes in S3-compatible object storage controlled by Niyān. The server implements or fronts the authenticated Git LFS endpoints and maps authorized batch operations to the configured object store.

The public model is an LFS object identifier and size, not an S3 bucket name or key. Bucket topology and key layout remain internal deployment details. The initial topology is defined by [ADR 0007](0007-shared-s3-bucket.md).

## Consequences

- Existing Git LFS clients, pointer files, object identifiers, and checkout behavior can be reused.
- Niyān avoids designing its own chunking, cache, and smudge/clean protocol in the first release.
- The server must implement authorization, batch negotiation, upload verification, retry behavior, and garbage-collection safety.
- Git LFS does not erase Git's scaling limits for repositories with very large path counts; the first version accepts those limits.
- Cross-dataset deduplication is not a product requirement and must not complicate the initial security model.
