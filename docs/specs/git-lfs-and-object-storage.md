# Niyān Git LFS and Object-storage Protocol

- **Status:** Accepted
- **Audience:** server, CLI, Python-client, and deployment maintainers
- **Last reviewed:** 2026-09-17

## Purpose

This specification defines the v1 contract between Git LFS clients, the Niyān control plane, and S3-compatible object storage. It covers object identity, batch negotiation, direct transfers, multipart uploads, upload finalization, retries, and the lifecycle of uploads that never become reachable from accepted Git history.

This specification follows [ADR 0003](../architecture/decisions/0003-git-lfs-s3-data-plane.md), [ADR 0005](../architecture/decisions/0005-direct-object-transfers.md), and [ADR 0007](../architecture/decisions/0007-shared-s3-bucket.md). It uses the standard [Git LFS pointer format](https://github.com/git-lfs/git-lfs/blob/main/docs/spec.md), [Batch API](https://github.com/git-lfs/git-lfs/blob/main/docs/api/batch.md), and [custom transfer-agent protocol](https://github.com/git-lfs/git-lfs/blob/main/docs/custom-transfers.md).

## Protocol Boundary

The Git LFS endpoint for a dataset is rooted at `/git/<dataset-uuid>.git/info/lfs`. The dataset UUID comes from trusted routing state and must never be replaced with a namespace path, dataset slug, token-supplied identifier, or object-supplied identifier when constructing storage keys.

The server supports the Git LFS Batch API at:

```text
POST /git/<dataset-uuid>.git/info/lfs/objects/batch
```

Git LFS batch requests use HTTPS Basic authentication as defined by the [authentication specification](authentication.md): the username is informational and the access token is the password. Downloads require `read_repository`; upload negotiation and finalization require `write_repository`. The server must also apply the token's resource boundary and the user's current dataset authorization.

Git LFS executes an upload's standard `verify` action separately and does not automatically reuse the batch request's repository credential for that action. Niyān therefore returns a short-lived signed Bearer capability in the verify action's `header` map. The capability is scoped to the issuing access-token record, dataset UUID, object identifier, and declared size. Verification must reject an expired or altered capability and must recheck that the underlying access token, account, resource boundary, scope, and current dataset role still permit the operation. The Batch response must never echo the long-lived access-token secret.

The server accepts `upload` and `download` batch operations. It must reject unsupported operations, malformed object identifiers, negative or inconsistent sizes, unsupported hash algorithms, and requests containing more than 100 objects. A client may transfer more than 100 objects through successive batches. An oversized request receives `413 Payload Too Large`; object-specific failures use the standard per-object Git LFS error shape when possible.

Batch requests and responses must use `application/vnd.git-lfs+json`. Responses must not expose bucket names, internal keys, provider credentials, backend errors, long-lived access tokens, or authorization details beyond short-lived action-scoped capabilities required by the standard protocol.

## Object Identity and Isolation

The canonical identity of a v1 Git LFS object is the tuple of:

- immutable dataset UUID;
- lowercase 64-character SHA-256 Git LFS object identifier; and
- non-negative byte size.

Git LFS pointer v1 supports only SHA-256. Niyān must reject another `hash_algo` rather than producing pointers that standard Git LFS clients cannot use. The SHA-256 object identifier is independent of whichever checksum algorithm the configured S3-compatible backend uses for transport integrity.

Objects are isolated by dataset. Identical bytes uploaded to two datasets produce two independently authorized stored objects; v1 does not deduplicate across datasets. The public API exposes the LFS object identifier and size, never an object-store key. Every object-store key must be derived from the trusted dataset UUID and validated object identifier under the installation-controlled prefix.

An existing object with the same dataset, object identifier, and size is reusable and makes an upload action unnecessary. Reusing an object must refresh neither its content nor its authorization. The same dataset and object identifier with a different declared size is an integrity error.

## Transfer Selection

The server supports the standard `basic` transfer adapter for downloads and for uploads below the multipart threshold. A basic upload is one signed HTTP `PUT`; a basic download is one signed HTTP `GET` and may use standard range requests for retry or resume behavior.

Niyān also defines the custom transfer adapter `niyan-multipart` for multipart uploads. The `niyan` CLI must configure this agent in each Niyān-managed dataset checkout and advertise it to the Batch API. The transfer agent is part of the unified `niyan` Python distribution; users are not expected to install another package or configure it manually.

The default multipart threshold is 100 MiB and is installation-configurable. If an upload batch contains any object at or above that threshold and the client advertises `niyan-multipart`, the server selects `niyan-multipart` for the entire batch. The custom agent must correctly handle every object in that batch, including smaller objects. Downloads continue to use `basic` because Git LFS already supports resumed HTTP downloads.

If a required multipart upload comes from a client that does not advertise `niyan-multipart`, the server returns an actionable per-object error. This preserves ordinary Git LFS compatibility for small transfers while making the Niyān CLI the supported path for resilient large uploads. An installation may set a lower effective threshold when its storage backend has a smaller single-request limit, but it must not set a threshold above the backend's maximum single-upload size.

The custom agent must request multipart initiation and signed part actions from Niyān, upload parts directly to object storage, complete the provider multipart upload, and notify Niyān of completion before reporting success to Git LFS. It must support bounded concurrency, incremental progress, retries of individual parts, and abort after a terminal failure. Raw storage credentials must never be returned to the agent.

For an upload batch using `niyan-multipart`, each large object's `upload` action points to its versioned Niyān REST initiation endpoint. The custom agent reads the same access token selected by the surrounding Niyān checkout and authenticates multipart initiation, part-action, completion, and abort requests with the Bearer scheme. The Batch response never embeds that long-lived token. Every multipart-control request rechecks token validity, `write_repository` scope, its optional dataset boundary, and the user's current contributor-or-higher role.

The REST contract is rooted at `/api/v1/datasets/<dataset-uuid>/lfs/objects/<oid>/multipart`. Initiation returns an opaque Niyān session UUID, exact server-selected part size and count, and expiry. Part-action requests address a one-based part number and exact byte size. Completion submits the ordered provider ETags and optional provider checksum metadata; abort addresses the opaque session UUID. Provider upload identifiers, storage keys, and credentials remain private server state.

If one upload batch requires multipart and advertises `niyan-multipart`, the server selects it for the whole batch. Smaller objects in that batch may retain ordinary signed single-PUT actions; the custom agent must execute those actions correctly. Multipart content parts always travel directly from the custom agent to signed object-store URLs. Django receives only bounded control metadata.

## Signed Transfer Actions

Django authenticates, authorizes, and orchestrates transfers but never proxies large-object bytes. Upload and download actions point directly to the configured private S3-compatible storage service. Multipart coordination endpoints may return signed actions for individual parts without returning storage credentials.

A transfer action must be scoped to exactly one object, operation, and storage key. Its default lifetime is 15 minutes and is installation-configurable. Clients must treat actions as bearer credentials, must not persist or log them, and must obtain a replacement after expiry rather than editing or extending an action themselves.

Signed upload actions must constrain all provider-supported properties that affect identity, including the storage key and expected content length. Signed download actions must not authorize listing the bucket or accessing neighboring keys. Redirects must not forward Niyān access tokens or signed headers to a different origin.

The storage backend is selected by installation configuration, not by the client, namespace, or dataset. Provider-specific capabilities and signing details belong behind the server's object-store interface. No behavior in this protocol may depend on one named S3 provider.

## Integrity and Upload Finalization

SHA-256 has two roles in Git LFS: it names the content in the pointer and lets Git LFS validate downloaded bytes before placing them in its local object store. Niyān retains this standard identity even when object storage uses another integrity algorithm.

After upload completion, Niyān must establish that:

1. the upload session belongs to the authenticated dataset and expected object identifier;
2. the exact object-key target exists;
3. its stored byte size equals the declared Git LFS size; and
4. any checksum that the configured backend claims to validate is consistent with the completed transfer.

When a backend supports a provider-validated SHA-256 checksum, Niyān should bind the expected LFS digest into the signed upload and verify the returned checksum metadata. Otherwise, Niyān may use a provider-supported transport checksum such as CRC32C or CRC32 to detect transfer corruption. A non-SHA checksum does not replace the Git LFS object identifier, and an S3 ETag must never be interpreted as a portable content hash.

S3-compatible services vary in their checksum extensions. V1 therefore does not require one optional provider checksum algorithm and does not download every new object through Django solely to calculate a second hash. The server must record which integrity evidence was actually validated and must not claim cryptographic server-side content verification when it performed only key, size, and provider transfer checks. Git LFS clients still validate complete downloads against the SHA-256 pointer identifier.

An object becomes `available` only after successful finalization. A Git ref update must not become visible when it introduces a pointer to an object that is not available for that dataset. The [Git write transport and ref-update policy](git-write-transport.md) defines how receive-pack discovers, leases, and enforces that set before ref visibility.

## Object Lifecycle and Failed Pushes

Object metadata uses these lifecycle states:

- `pending`: Niyān has negotiated an upload that has not completed successfully;
- `available`: transfer finalization succeeded, but no accepted Git ref is known to require the object; and
- `referenced`: an accepted Git ref update made the object reachable.

Negotiation and finalization must be idempotent. Retrying the same dataset, object identifier, and size must resume or reuse safe state rather than create ambiguous stored objects. A failed or expired multipart session may be replaced only after its provider upload has been aborted or made unreachable.

Git LFS upload precedes the Git ref update, so a successful upload may remain `available` after the Git push fails. Such an object is retained for a seven-day grace period, during which a retry may reuse it. After the grace period it becomes eligible for automatic orphan cleanup.

Niyān ships a maintenance worker as part of the server deployment. The worker periodically claims eligible records through PostgreSQL, rechecks that they are still unreferenced, deletes their stored objects or aborts incomplete multipart uploads, and then removes their metadata. Cleanup must be retry-safe, must not require Redis or Celery in v1, and must never delete an object merely because a transient Git or object-store operation failed. Docker Compose and production deployment guidance must run this worker by default so operators do not need to create an external cron job.

The server exposes the worker as the `run_lfs_maintenance` management command. Its default mode is a long-running process; `--once` performs one bounded explicit reconciliation pass and exits.

This failed-push cleanup is not full repository garbage collection. Discovering objects made unreachable by branch deletion, force-push, or later history rewriting requires a complete Git reachability scan and is outside this specification. Niyān must not automatically purge those objects until that algorithm and its retention rules are separately accepted.

Permanent dataset deletion must eventually delete every object and incomplete multipart upload beneath that dataset's UUID-scoped prefix before removing the final deletion state. If bucket versioning is enabled, deletion must also remove retained object versions; installations for which Niyān cannot do so must disable bucket versioning for the Niyān prefix in v1.

## Retry and Failure Behavior

The following operations must be safe to retry with the same object identity:

- batch negotiation;
- multipart initiation after an expired or conclusively aborted session;
- signed-part URL acquisition;
- upload finalization;
- download-action acquisition; and
- worker cleanup.

A retry must never change the target dataset or object key. Clients should retry transient storage and network failures with bounded exponential backoff and must reacquire expired signed actions. Authentication, authorization, validation, checksum, and size failures are terminal until their input or credential changes.

Public failures must distinguish an invalid request, denied credential, expired transfer action, missing object, size or integrity mismatch, unavailable storage service, and unsupported transfer capability without exposing whether an inaccessible dataset or object exists. Provider response bodies, internal keys, multipart upload identifiers, and signed URLs must not appear in public errors or logs.

## Security Boundary

- The bucket and all dataset objects are private; provider public-sharing URLs are never authorization.
- Every batch, multipart-control, finalization, and action-refresh request is authenticated and authorized before object metadata or a signed action is returned.
- Signed actions are narrow bearer credentials and must be redacted from application, proxy, worker, and client logs.
- Object identifiers, sizes, multipart part numbers, completion metadata, and provider responses are untrusted input.
- Object-store operations must use trusted dataset UUID-derived prefixes and must not accept caller-selected keys.
- A user's current access, token scopes, and token resource boundary are reevaluated when a new action is issued. An already issued signed action remains valid only until its short expiry.

## Initial Non-goals

- A replacement pointer format or hash algorithm unsupported by standard Git LFS.
- Cross-dataset deduplication.
- Content-defined chunking.
- Client-side encryption that prevents Niyān from interpreting pointer content.
- Public buckets or provider public-sharing links.
- A non-S3 bulk-data backend.
- Periodic whole-bucket corruption scrubbing.
- Full reachability garbage collection after ref deletion or history rewriting.
- Treating storage checksum support as uniform across S3-compatible providers.

## Acceptance Scenarios

An implementation of this specification must demonstrate at least:

- an authorized basic upload, finalization, Git push, and download;
- direct byte transfer in which Django handles no bulk request body;
- a multipart upload selected at the threshold, including retry of an interrupted part;
- rejection of a batch containing more than 100 objects;
- rejection of an unsupported hash algorithm and an inconsistent object size;
- idempotent reuse of an already available object;
- denial for an expired, revoked, wrongly scoped, or wrong-dataset credential;
- rejection of a ref update that introduces an unavailable LFS object;
- cleanup of a failed-push upload only after the seven-day grace period; and
- sanitized failures that contain no storage credentials, signed URLs, internal keys, or provider response body.
