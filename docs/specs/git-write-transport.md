# Niyān Git Write Transport and Ref-update Policy

- **Status:** Accepted
- **Audience:** server, CLI, authorization, and deployment maintainers
- **Last reviewed:** 2026-09-18

## Purpose

This specification defines the v1 write path for Git-backed datasets. It covers authenticated smart-HTTP receive-pack, the initial ref policy, concurrent updates, Git LFS availability enforcement, server-controlled hooks, and client-visible failures.

Git remains authoritative for commits, trees, refs, object connectivity, and fast-forward semantics. Niyān authorizes a push and applies dataset policy around `git receive-pack`; it does not implement the Git wire protocol or create a second ref transaction model.

This specification follows [ADR 0002](../architecture/decisions/0002-git-native-datasets.md), [ADR 0006](../architecture/decisions/0006-git-repository-hosting.md), the [authentication specification](authentication.md), the [authorization specification](authorization.md), and the [Git LFS and object-storage protocol](git-lfs-and-object-storage.md). Git's receive-pack and hook behavior is defined by the upstream [git-receive-pack](https://git-scm.com/docs/git-receive-pack) and [githooks](https://git-scm.com/docs/githooks) documentation.

## Smart-HTTP Write Endpoint

A dataset accepts pushes through Git smart HTTP at its immutable UUID-derived repository URL. The server accepts:

```text
GET  /git/<dataset-uuid>.git/info/refs?service=git-receive-pack
POST /git/<dataset-uuid>.git/git-receive-pack
```

Dumb HTTP, SSH, direct filesystem access, and an independently implemented receive protocol are outside v1. Both the recommended Niyān CLI and standard Git clients use this supported smart-HTTP endpoint.

Both receive-pack advertisement and execution require HTTPS Basic authentication. The username is informational and the access token is the password. The credential must grant `write_repository`, its resource boundary must include the dataset, and its owner must currently have at least the `contributor` role. `write_repository` includes the corresponding read access required by Git negotiation.

Missing, invalid, expired, or revoked credentials receive `401 Unauthorized` with a Basic authentication challenge. An unknown dataset and a dataset invisible to the authenticated user produce the same not-found response. A valid credential that lacks `write_repository`, targets another dataset, or belongs to a visible dataset reader receives a generic forbidden response.

After authorization, Django invokes `git http-backend` with `GIT_HTTP_EXPORT_ALL` and receive-pack enabled only for the resolved UUID-derived repository. It streams request and response bodies, forwards the negotiated Git protocol header, and passes no access token or Authorization header to Git or a hook.

## Initial Ref Policy

V1 accepts mutations only beneath:

- `refs/heads/` for branches; and
- `refs/tags/` for tags.

All other ref namespaces, symbolic-ref updates, malformed ref names, and attempts to update `HEAD` through receive-pack are rejected. A branch target must be a commit. A tag may be annotated or lightweight, but it must ultimately peel to a commit. The `niyan tag create` command creates annotated tags by default.

The initial role and mutation policy is:

| Operation | Minimum role | Rule |
| --- | --- | --- |
| Create a branch | Contributor | The target must be a commit. |
| Fast-forward a branch | Contributor | The old commit must be an ancestor of the new commit. |
| Delete a non-default branch | Contributor | The dataset's default branch is excluded. |
| Create a tag | Contributor | The target must peel to a commit. |
| Update an existing tag | Nobody | Tags cannot be changed in place. |
| Delete a tag | Maintainer | Owners are included by role ordering. |
| Delete the default branch | Nobody | The default branch must remain present after its first creation. |
| Non-fast-forward any branch | Nobody | Force updates are not supported in v1. |

The unborn `main` branch in a new dataset may be created by a contributor. Once created, `main` is the default branch and may receive contributor fast-forward pushes directly because v1 has no merge-request workflow. It cannot be deleted or force-updated.

The same fast-forward-only rule applies to every branch in v1. Later protected-ref rules may require a higher role or impose additional restrictions on selected branches and tags, but they must not silently weaken the baseline above. A future force-with-lease capability requires an explicit specification update and server-side policy; plain force push is not accepted.

Deleting a tag and later creating another tag with the same name are two separately authorized operations. This administrative escape hatch does not make an in-place tag update valid, and clients must present tag deletion as destructive.

## Ref Transactions and Concurrency

Each proposed update includes the ref value the client observed. Git must compare that expected old object identifier with the current ref while holding its normal ref lock. A stale writer is rejected; Niyān must not automatically merge, rebase, retry against a newer tip, or overwrite the competing update.

The Niyān CLI must request Git's atomic push capability whenever it sends more than one ref update, and the server must advertise atomic push support. The pre-receive policy evaluates the complete proposed ref set and rejects the entire push when any update violates authorization, ref, or LFS policy. Niyān commands must not report a multi-ref push as successful unless Git reports every requested update as accepted.

Standard Git clients that omit atomic mode may receive Git's native per-ref transaction behavior after the all-or-nothing pre-receive checks pass. Niyān never attempts to repair or conceal a partial result caused by a later Git ref-lock race; it reports Git's result accurately. Clients that need all-or-nothing publication of several refs must request Git's atomic capability.

Git's object connectivity checks must remain enabled. Newly received objects stay in Git's quarantine area until pre-receive succeeds. A rejected push must not move quarantined objects into the repository's main object store or update any ref.

## Pre-receive Enforcement

Niyān installs one server-controlled pre-receive hook outside every dataset repository and selects it through administrator-owned Git configuration. Dataset contents and repository writers must not be able to replace, bypass, or add server-side hooks. User-controlled client hooks remain ordinary local Git behavior and are not trusted by the server.

For the complete proposed update set, pre-receive must:

1. validate every old object identifier, new object identifier, and ref name supplied by Git;
2. load the authenticated push context and recheck the user's current role, token scope, token resource boundary, and dataset deletion state;
3. enforce the allowed namespaces, target object types, default-branch rule, tag rule, deletion rule, and fast-forward-only rule;
4. enumerate every commit made newly reachable by the accepted tips, including commits visible through Git's quarantine environment;
5. inspect the Git trees of those commits for valid Git LFS pointer blobs;
6. deduplicate their dataset-scoped LFS object identifiers and declared sizes;
7. require every referenced LFS object to be `available` or `referenced` for this exact dataset; and
8. acquire a short-lived push lease for every available object needed by the transaction before allowing receive-pack to continue.

The scan must cover complete newly reachable history, not only the final tree, so a clone of any accepted commit cannot depend on a missing LFS object. An object already reachable from an existing ref need not be rescanned merely because a new ref points to it. Implementations may optimize traversal and database queries in bounded batches, but optimization must not change this acceptance rule.

The hook recognizes canonical Git LFS pointer blobs independently of filename or dataset format. Enforcing which ordinary files should have been tracked by Git LFS belongs to the separate LFS tracking policy; this hook's availability check must not invent that policy.

If any required LFS object is absent, pending, has an inconsistent size, belongs to another dataset, or cannot be checked because storage metadata is unavailable, the entire push is rejected. The error identifies the affected ref and a bounded number of object identifiers and tells the user to upload the missing objects with Niyān or Git LFS before retrying the push; it must not reveal storage keys or provider details.

## Push Context and LFS Leases

Before invoking receive-pack, Django creates a short-lived, single-use push context bound to the authenticated user, access-token identifier, dataset UUID, effective role, request identifier, and expiry. Git and the hooks receive only the opaque context identifier plus the trusted dataset UUID; they never receive the access-token secret.

The pre-receive hook consumes or locks that context and records short-lived leases for required `available` LFS objects. The maintenance worker must skip an object while an unexpired push lease exists. This closes the race in which failed-push cleanup could otherwise delete an object after pre-receive validates it but before Git publishes the ref.

On a failed receive, the wrapper releases the leases or lets their short expiry elapse. On success, post-receive records the accepted old object identifier, new object identifier, and ref name, promotes the transaction's required LFS objects to `referenced`, and schedules rebuildable repository indexing. These operations must be idempotent.

Git ignores post-receive failures after refs have changed, so a post-receive failure must never make the CLI believe that an accepted ref was rolled back. Reconciliation must be able to replay accepted ref events and discover reachable LFS objects before cleanup. The cleanup worker's final reachability check remains authoritative when a metadata update was missed.

## Hook and Process Security

- Hook programs and their configuration are installation-owned, versioned with the server, and not writable through a dataset push.
- Git subprocesses are invoked without a shell and receive a minimal allowlisted environment.
- Push-context identifiers are random, short-lived, single-use bearer values and must not appear in logs or public errors.
- Hooks must not trust caller-supplied environment variables, push options, ref names, object identifiers, commit contents, pointer contents, or Git diagnostic output.
- Push options have no Niyān policy meaning in v1 and must not bypass an ordinary request or hook check.
- Policy diagnostics written to Git's sideband are bounded and sanitized.
- Backend standard error, filesystem paths, database details, hook tracebacks, object-store responses, signed URLs, and credentials must not reach the client.

## Client-visible Failures

The CLI must translate common receive failures into concise Niyān diagnostics while preserving a non-zero exit status. Stable failure classes include:

- authentication required or credential rejected;
- token missing `write_repository`;
- current role cannot push to the dataset or perform the requested ref operation;
- stale or non-fast-forward branch update;
- default-branch deletion denied;
- tag update or unauthorized tag deletion denied;
- unsupported ref namespace or target object type;
- required LFS objects unavailable or inconsistent;
- another push won the ref race; and
- repository or policy service temporarily unavailable.

Errors should name the human-meaningful ref when safe. Authentication and lookup failures must preserve the non-disclosure rules from the authentication and authorization specifications. A server failure must not be presented as a policy denial, and a rejected update must never be presented as a successful push.

## Initial Non-goals

- Git over SSH.
- Merge requests, approvals, or server-side merging.
- Force push or force-with-lease.
- User-provided server hooks.
- Push certificates or commit-signature policy.
- Branch-level read confidentiality.
- Arbitrary ref namespaces, Git notes, replace refs, or remote symbolic-ref mutation.
- Configurable protected-ref patterns; their later management surface may only make the accepted baseline stricter unless this specification is revised.
- Synchronous web indexing as part of the ref transaction.
- Full LFS garbage collection after branch deletion or history rewriting.

## Acceptance Scenarios

An implementation of this specification must demonstrate at least:

- creation of the unborn `main` branch by an authorized contributor;
- an authorized fast-forward push to `main` after all required LFS objects become available;
- creation and deletion of a non-default branch by a contributor;
- creation of a tag by a contributor and deletion by a maintainer;
- denial of reader, wrong-dataset, wrongly scoped, expired, and revoked credentials;
- rejection of a non-fast-forward update, default-branch deletion, tag update, and unsupported ref namespace;
- rejection of the entire proposed update set when one ref or LFS object fails policy;
- rejection before ref visibility when a newly reachable commit contains an unavailable LFS pointer;
- a concurrent stale push losing without overwriting the accepted ref;
- a push lease preventing cleanup from racing a successful ref publication;
- reconciliation after post-receive metadata work fails; and
- sanitized public output containing no credential, push context, internal path, storage key, provider response, or backend traceback.
