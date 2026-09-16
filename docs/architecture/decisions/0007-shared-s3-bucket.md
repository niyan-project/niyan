# ADR 0007: Use One S3-compatible Bucket with Dataset-scoped Keys

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

Creating one bucket per dataset would couple the dataset lifecycle to provider-specific bucket provisioning, quotas, global naming rules, credentials, lifecycle configuration, and deletion behavior. Niyān already provides the authorization boundary and does not mirror users or permissions into object storage.

## Decision

Configure one S3-compatible bucket per Niyān installation. Store objects below an installation-controlled root and an immutable dataset UUID prefix. The object-key layout remains private implementation detail, but every v1 key must be attributable to exactly one dataset without consulting a mutable namespace or dataset name.

Initial development may use a remote S3-compatible service; a local object-storage container is not required. Endpoint, bucket, credentials, and optional provider settings come from environment configuration and are never committed.

Private Niyān data is accessed through short-lived signed transfer URLs. A provider's public-sharing URL is not an authorization mechanism and must not be used for private dataset reads or writes.

## Consequences

- Creating, renaming, and deleting a dataset does not create or rename buckets.
- Operators configure credentials, lifecycle rules, and CORS once per installation rather than once per dataset.
- Dataset isolation depends on Niyān authorization and correct key construction, so object keys must always derive from trusted dataset UUIDs.
- Cross-dataset deduplication is not provided because each dataset has a distinct prefix.
- Dataset deletion and garbage collection can enumerate one prefix but must still respect retention and legal-purge policy.
- Deployments that need stronger physical isolation require a future storage-topology decision rather than an undocumented per-dataset exception.

