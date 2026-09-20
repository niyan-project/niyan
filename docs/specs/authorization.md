# Niyān Authorization

- **Status:** Accepted
- **Audience:** server, web, CLI, Python client, and deployment maintainers
- **Last reviewed:** 2026-09-20

## Purpose

This specification defines one authorization policy shared by REST, Git, Git LFS, repository browsing, and future object-transfer endpoints. HTTP handlers and storage adapters must ask this policy for decisions rather than infer access from namespace shape themselves.

Authentication identifies a user and may impose token scopes and a token resource boundary. Ordinary authorization then evaluates the user's current Niyān roles. Effective access is the intersection of both layers. Separately, staff operators may receive installation-wide authority from Django model permissions assigned directly or through Django auth groups; the System administration endpoints themselves require a browser session.

## Principals and Roles

A permission may come from:

- ownership of a personal namespace;
- membership in the dataset's containing group namespace or one of its ancestor groups;
- a direct grant from the dataset to a user; or
- a grant from the dataset to a Niyān group namespace of which the user is a member.

Dataset grants target exactly one user or one group namespace. Django auth groups are never dataset principals. Group grants use immutable namespace UUIDs, and direct grants use immutable user identities.

The initial ordered roles are `reader`, `contributor`, `maintainer`, and `owner`:

| Capability | Reader | Contributor | Maintainer | Owner |
| --- | --- | --- | --- | --- |
| View dataset metadata and browse repository | yes | yes | yes | yes |
| Clone, fetch, and download repository content | yes | yes | yes | yes |
| Create and fast-forward branches or create tags | no | yes | yes | yes |
| Delete a non-default branch | no | yes | yes | yes |
| Delete a tag | no | no | yes | yes |
| Manage configurable protected-ref rules | no | no | yes | yes |
| Change dataset name or slug | no | no | yes | yes |
| Create datasets in a group namespace | no | no | yes | yes |
| Manage dataset grants | no | no | no | yes |
| Permanently delete a dataset | no | no | no | yes |

No role may force-update a branch, update an existing tag in place, or delete the default branch in v1. The accepted [Git write transport and ref-update policy](git-write-transport.md) defines these baseline rules. Later configurable protection may make a selected ref stricter but must not silently weaken that baseline.

The owner of a personal namespace has the `owner` role for its datasets. For a group-owned dataset, the highest membership role inherited from the containing namespace or any ancestor group applies.

## Grant Evaluation

A direct user grant contributes its assigned role. A group grant is capped by both the member's effective role within the granted group and the grant's assigned role. For example, a group owner receiving a `reader` dataset grant is a reader of that dataset, while a reader in a group receiving a `contributor` grant remains a reader.

When several paths grant access, the highest resulting role wins. Revoking a membership or grant takes effect on the next request. Grants do not copy permissions into access tokens and do not survive deletion of their user, group, or dataset.

Nested group membership flows from ancestor to descendant namespaces. Membership in a subgroup does not grant access upward to its parent.

## Visibility and Non-disclosure

A dataset is visible when the user has at least reader access. An inaccessible private dataset must produce the same public not-found response as an unknown dataset for ordinary resource lookup. Credential-level failures, such as a valid token lacking a required scope or targeting another dataset, remain forbidden responses because the caller already possesses an identifiable credential restriction.

A namespace is visible to its personal owner, to a member of that group or an ancestor group, or when it contains at least one dataset visible through an explicit grant. Dataset listings include only datasets visible to the caller.

## Policy Boundary

The server exposes explicit policy operations for:

- viewing a namespace;
- creating a root or nested group;
- managing a group's identity and direct memberships;
- creating a dataset in a namespace;
- reading a dataset and its repository;
- writing repository refs;
- changing dataset metadata;
- managing dataset grants; and
- deleting a dataset.

Domain services remain responsible for enforcing the relevant policy inside their transaction. Selectors may use the same policy to avoid returning inaccessible objects, but a selector is not a substitute for authorization at mutation time.

Django superusers may bypass Niyān product policy for installation recovery and administration. A non-staff user never gains product authority from Django model permissions. A staff user may exercise only the following explicitly mapped installation-wide permissions:

| Django model permission | Niyān system authority |
| --- | --- |
| `accounts.view_user` | List installation users |
| `accounts.add_user` | Provision an active, non-staff user and personal namespace |
| `namespaces.view_namespace` | Discover and view all groups |
| `namespaces.add_namespace` | Create groups anywhere in the hierarchy |
| `namespaces.change_namespace` | Rename groups and manage direct memberships |
| `namespaces.delete_namespace` | Permanently delete empty groups |
| `datasets.view_dataset` | Discover, browse, clone, fetch, and download all datasets |
| `datasets.add_dataset` | Create datasets in any namespace |
| `datasets.change_dataset` | Change metadata, push repository refs, and manage grants and protected-ref rules |
| `datasets.delete_dataset` | Permanently delete datasets |

These permissions augment rather than replace ordinary Niyān roles. They are evaluated by the same server policy used by REST, Git, Git LFS, browsing, and transfer authorization; hiding a System navigation item is never the authorization boundary. Access-token authentication does not expose the staff-only System API, although Git and data operations performed with a user's access token still evaluate that user's current resource authority and the token's scope and resource boundary.

Any authenticated user may create a root group. In ordinary collaboration, only an effective owner of a group may create a child group beneath it or manage that group's identity and direct memberships. A staff operator may instead perform the precisely mapped installation-wide operation above. Niyān roles do not grant Django-admin access, and staff authority must never be materialized as a namespace membership or dataset grant.

## Initial Grant API

The versioned REST API allows dataset owners to list, create, update, and delete grants. A grant response identifies the immutable dataset and principal, principal type, current human-facing principal path or username, assigned role, and timestamps.

Grant mutations require browser session or `api` token scope, the token's dataset boundary must permit the target dataset, and the authenticated user must currently own access to that dataset. Grant reads require `read_api` and owner access because membership information is administrative metadata.

The API rejects grants to personal namespaces, grants that select both or neither principal type, duplicate grants, and grants to the dataset's containing namespace because its membership already supplies access.

## Initial Non-goals

- Path-level authorization.
- Branch-level confidentiality.
- Deny rules or role subtraction.
- Public and installation-visible datasets.
- Custom roles.
- Service accounts or external identity-provider groups.
