# Niyān Namespaces and Datasets

- **Status:** Accepted
- **Audience:** server, web, CLI, and API maintainers
- **Last reviewed:** 2026-09-20

## Purpose

This specification defines the control-plane identity of namespaces and datasets and the initial lifecycle for creating a dataset repository. Git remains authoritative for the dataset's files, commits, branches, and tags; the records described here identify and authorize access to the repository rather than duplicating its history.

This specification follows [ADR 0002](../architecture/decisions/0002-git-native-datasets.md) and [ADR 0006](../architecture/decisions/0006-git-repository-hosting.md).

## Namespaces

A namespace provides a human-readable location for datasets. Every namespace must have:

- an immutable UUID used by internal relationships and stable API references;
- a kind of `personal` or `group`;
- a display name;
- a normalized lowercase slug; and
- an optional parent namespace.

A personal namespace must belong to exactly one user and must be a root namespace. A user must have exactly one personal namespace. Its initial slug should be the user's username, but renaming the namespace must not change its UUID or the identity of datasets beneath it.

A group namespace has no owning user and may be a root namespace or a child of another group namespace. Personal namespaces must not contain child namespaces.

Users join group namespaces through Niyān namespace memberships. A membership records one of four role names: `owner`, `maintainer`, `contributor`, or `reader`. This specification defines their persistence and ordering vocabulary; the authorization specification must define their exact allowed operations before group access is exposed publicly.

Any authenticated user may create a root group. The creator becomes a direct owner. An owner may create a nested group beneath that group; the nested group's creator likewise becomes a direct owner. A group must retain at least one direct owner, even when inherited ownership would otherwise provide access.

Owners may rename a group, change its slug, manage direct memberships, and permanently delete it once it contains no child groups or datasets. Direct membership records may be created, assigned another role, or removed. Inherited membership is computed from ancestors and is never materialized as an editable membership in a descendant.

V1 membership management adds an existing installation user by exact username. It does not send email, create pending invitations, or allow a person who does not already have an administrator-created account to register through an invitation.

A slug must be unique among the datasets and child namespaces directly beneath the same namespace. Root namespace slugs must be unique installation-wide. This shared path constraint prevents a path from ambiguously naming both a subgroup and a dataset.

The full namespace path is derived from ancestor slugs. It is a mutable locator for people and Git remotes, not a persistent identifier. Moving or renaming a group changes its human-facing path while preserving all namespace and dataset UUIDs.

## Django Groups and Niyān Groups

Django's `auth.Group` model is framework-level authorization machinery. Niyān uses it to assemble installation-wide staff-operator permissions and Django-admin permissions, but it must not represent a Niyān group, namespace membership, dataset grant, or repository ACL.

A Niyān group is a namespace whose kind is `group`; there is no second Niyān model named `Group`. Niyān group membership is stored explicitly as a relationship between a user and a group namespace. Ordinary resource authorization for REST, Git, Git LFS, and object transfer uses Niyān namespace memberships and dataset grants. The explicit exception is an authenticated staff operator exercising an installation-wide Django model permission through the System surface or the same underlying resource API.

The two systems must not mirror or synchronize membership. Django groups must not be exposed through the Niyān group API, and Niyān group membership must not silently grant Django-admin permissions.

## Datasets

A dataset control-plane record must have:

- an immutable UUID;
- a containing namespace;
- a normalized lowercase slug;
- a display name;
- an optional short description;
- the user who created it; and
- creation and modification timestamps.

The pair of namespace and dataset slug must be unique. Dataset UUIDs, rather than mutable namespace paths, must be used for repository storage and durable internal references.

The initial implementation creates private datasets only. Public and installation-visible datasets require an accepted authorization specification before they become selectable behavior. Installation-wide staff authority is an operator capability, not a visibility level and not a synthetic Niyān role.

The database record must not store branches, tags, commits, directory entries, or another dataset version identifier. Those belong to Git. Rebuildable indexes may be introduced separately when browsing performance requires them.

## Repository Storage

The server must read the required repository-root path from `NIYAN_GIT_ROOT`. Each dataset repository must be a standard bare Git repository stored at `<NIYAN_GIT_ROOT>/<dataset-uuid>.git`. Namespace and dataset slugs must never be interpolated into filesystem paths.

New repositories must use `main` as the unborn initial branch. Repository initialization must invoke Git without a shell and must treat paths, process output, and exit status as untrusted input.

The repository root is durable application data. It must not live inside the source checkout by default and must be writable only by the application account and deployment administrators.

## Creation Lifecycle

Dataset creation must be implemented by a domain service rather than an HTTP handler or model signal. Within one database transaction, the service must:

1. authorize creation in the selected namespace;
2. validate and reserve the dataset identity and slug;
3. initialize a bare repository in a temporary directory beneath the configured repository root;
4. atomically rename the completed temporary repository to its UUID-derived final path; and
5. commit the dataset record only after repository initialization succeeds.

An ordinary validation, Git, filesystem, or database failure must leave neither a visible dataset record nor a repository created by the failed operation. Cleanup must only remove paths created by that operation and must never recursively remove the configured repository root.

PostgreSQL and the repository filesystem cannot form one atomic transaction. A process or host failure may therefore leave an unreferenced repository after its atomic rename but before the database commit. Such a repository must remain unreachable through Niyān and must be recoverable or removable by a future explicit reconciliation command. The initial creation service does not require a background worker or Redis.

The service must reject an existing final repository path rather than adopting or overwriting it. Registering existing repositories is outside this initial workflow.

## Initial API Boundary

The public API must be versioned under `/api/v1/`. Dataset creation accepts an immutable namespace UUID together with the dataset slug, display name, and optional description of at most 500 characters. A successful response returns the immutable dataset and namespace UUIDs, current namespace path, slug, display name, description, initial default branch, caller's Niyān role when one exists, explicit write, update, access-management, and deletion capability booleans, and creation timestamp. The capability booleans are authoritative for user-interface actions because staff model permissions do not masquerade as Niyān roles.

The endpoint must require an authenticated user and call the domain service rather than performing Git or filesystem work directly. Dataset creation is allowed in the caller's personal namespace or a group where the caller has at least maintainer access. Scoped tokens, visibility rules, and protected-ref policy are defined by their focused specifications.

An authenticated user may retrieve a dataset in their personal namespace by immutable dataset UUID. They may also list datasets in their personal namespace by immutable namespace UUID using limit-and-offset pagination. Dataset lists are ordered by creation timestamp and then UUID so repeated requests have a deterministic order. The initial page limit must not exceed 100 datasets.

Path-oriented clients may resolve a visible human-facing namespace path to its immutable UUID through `/api/v1/namespaces/resolve`. The general dataset list may omit `namespace_id` to return a paginated list of every dataset visible to the caller. These lookups apply the same visibility rules as dataset access and never expose inaccessible namespaces or datasets.

The namespace API lists visible personal namespaces and groups and exposes immutable-UUID operations to create, retrieve, update, and delete groups. Group owners may list, create, update, and delete direct memberships through child membership endpoints. Expected conflicts include an occupied group path, a non-empty group, a duplicate membership, and an operation that would remove or demote the final direct owner.

An authenticated owner or maintainer may update a dataset's display name, slug, description, or any combination of those fields. An empty description clears it. Changing a slug changes its human-facing path but does not change the dataset UUID or UUID-derived repository location. Empty updates, null values, invalid slugs, overlong descriptions, and conflicting namespace paths must be rejected.

An authenticated owner may permanently delete a dataset. Deletion is irreversible: the control-plane record and bare Git repository must be removed, and the same contract will apply to dataset-owned Git LFS objects when LFS storage is implemented. Confirmation of the exact dataset path belongs in interactive clients such as the web application; the authenticated API operation itself does not implement a confirmation prompt.

Deletion may use a short-lived internal state to coordinate durable metadata and repository storage, but that state is not archival and cannot be restored. A dataset undergoing deletion must immediately become unavailable to ordinary reads and updates. Interrupted deletions must be safe to retry, and the control-plane record must not be removed until repository deletion succeeds.

Read and update operations must not reveal whether another user's private dataset or namespace exists. An inaccessible identity has the same response as an unknown identity. This rule must remain true when group authorization is added.

Expected client-visible failures are unauthenticated access, an unknown namespace, denied namespace access, an invalid slug, a conflicting path, and repository provisioning failure. Responses must not disclose repository filesystem paths, Git process output, database details, or credentials.

## Scope Boundaries

This lifecycle specification does not define the following behavior. Later focused specifications may define and implement it without superseding the namespace and dataset model here.

- Group and dataset role evaluation, which is defined by [Authorization](authorization.md).
- Registering or importing an existing Git repository.
- Browser file upload or initial commits.
- Git smart HTTP and repository browsing, which are defined by [Repository access](repository-access.md).
- Git LFS or S3 transfer endpoints.
- Repository metadata indexing.
- Dataset transfer, export, or namespace moves.
- Public visibility and anonymous access.

## Compatibility Requirements

Namespace and dataset UUIDs must remain stable across renames and moves. Repository layout changes must preserve the UUID-to-repository mapping or provide an explicit migration. API clients must not be required to understand the physical repository path.

The on-disk repository must remain usable by standard Git tooling independently of Niyān-specific metadata.
