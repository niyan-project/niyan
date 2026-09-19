# Audit Events

- **Status:** Accepted
- **Audience:** server, web, security, and operations maintainers
- **Last reviewed:** 2026-09-18

## Purpose

Niyān records durable, queryable events for security-sensitive identity changes and collaborative dataset mutations. The log explains who attempted what, against which Niyān resource, when, and whether it was accepted. It is not an application debug log and must not become a secondary store for secrets or dataset content.

## Event Model

Every event has an immutable UUID, server timestamp, stable action name, accepted or rejected outcome, optional bounded public reason code, request identifier where available, actor user and access-token identifiers where available, and immutable identifiers plus safe display snapshots for the affected installation, group, dataset, ref, token, or draft.

The event payload is a versioned, bounded JSON object containing only action-specific non-secret metadata. It may contain role names, ref names, object identifiers, dataset or group paths, and changed-field names. It must never contain passwords, access-token secrets, session identifiers, authorization headers, push contexts, signed URLs, object-storage keys, file content, commit messages, raw provider responses, or backend tracebacks.

V1 records at least:

- access-token issuance, expiry, revocation, and rejected use where an identifiable token exists;
- direct group membership creation, role change, and removal;
- dataset grant creation, role change, and revocation;
- protected-ref rule creation, update, and deletion;
- accepted and policy-rejected branch and tag mutations;
- browser draft commitment and rejection;
- permanent dataset deletion; and
- administrator recovery actions that bypass normal product policy.

The stable v1 action names are `access_token.issued`, `access_token.expired`, `access_token.revoked`, `access_token.use_rejected`, `group.created`, `group.updated`, `group.deleted`, `group.membership_created`, `group.membership_role_changed`, `group.membership_removed`, `dataset.created`, `dataset.updated`, `dataset.deleted`, `dataset.grant_created`, `dataset.grant_role_changed`, `dataset.grant_revoked`, `dataset.protected_ref_created`, `dataset.protected_ref_updated`, `dataset.protected_ref_deleted`, `git.ref_mutation`, and `browser_draft.commit`. A later action name or payload version is an additive protocol change; repurposing an existing name is not.

Routine successful reads, every signed transfer action, and raw object-storage requests are not audit events in v1 because their volume would obscure collaboration history and risk recording transfer capabilities. Operational access logs remain a deployment concern.

## Durability and Immutability

Events are append-only through application code. Product APIs, ordinary Django admin actions, and model services provide no update or delete operation. Accepted database-backed mutations record their event in the same PostgreSQL transaction whenever possible. Git ref events are recorded by the trusted receive hooks and idempotent reconciliation path because Git and PostgreSQL cannot share a transaction.

A failure to durably record a required event fails a security-sensitive control-plane mutation before it becomes visible when the event can share that mutation's transaction. Post-receive audit failure cannot roll back an accepted Git ref; reconciliation must recover the event from the accepted ref record without duplicating it.

Events are retained indefinitely in v1. Future archival or retention limits require an explicit specification update and must preserve the promised administrative record. Database permissions, restricted application operations, backups, and deployment controls provide tamper resistance. Niyān does not claim that rows are cryptographically tamper-proof against a PostgreSQL administrator.

## Visibility

Django superusers can inspect every event through a read-only administrative view. A dataset owner may list events scoped to that dataset, and a group owner may list membership and group-identity events scoped to that group. Product-facing queries never reveal an otherwise inaccessible resource, secret metadata, administrator-only recovery detail, IP address, or user-agent string.

Dataset and group event APIs use deterministic reverse-chronological pagination and require `read_api` plus current owner access. Losing ownership immediately removes audit visibility but does not remove retained events. Users can continue to see their own access-token lifecycle in the existing security interface without receiving unrelated installation audit data.

The v1 query routes are `GET /api/v1/datasets/{dataset_id}/audit-events`, `GET /api/v1/namespaces/{namespace_id}/audit-events`, and `GET /api/v1/auth/audit-events`. Pages accept bounded `limit` and `offset` parameters and order equal timestamps by immutable event UUID. The existing maintenance worker materializes time-based access-token expiry events, while an attempted use records expiry immediately when necessary.

## Non-goals

- A general-purpose logging or observability backend.
- Cryptographic non-repudiation or protection from database administrators.
- Recording complete HTTP requests, Git packets, file contents, or downloads.
- User-configurable retention or external SIEM delivery in v1.
