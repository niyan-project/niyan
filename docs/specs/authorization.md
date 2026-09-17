# Niyān Authorization

- **Status:** Accepted
- **Audience:** server, web, CLI, Python client, and deployment maintainers
- **Last reviewed:** 2026-09-17

## Purpose

This specification defines one authorization policy shared by REST, Git, Git LFS, repository browsing, and future object-transfer endpoints. HTTP handlers and storage adapters must ask this policy for decisions rather than infer access from namespace shape themselves.

Authentication identifies a user and may impose token scopes and a token resource boundary. Authorization then evaluates the user's current Niyān roles. Effective access is the intersection of both layers.

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
- creating a dataset in a namespace;
- reading a dataset and its repository;
- writing repository refs;
- changing dataset metadata;
- managing dataset grants; and
- deleting a dataset.

Domain services remain responsible for enforcing the relevant policy inside their transaction. Selectors may use the same policy to avoid returning inaccessible objects, but a selector is not a substitute for authorization at mutation time.

Django superusers may bypass Niyān product policy for installation recovery and administration. Staff status and Django model permissions alone do not grant product access.

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
