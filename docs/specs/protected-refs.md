# Protected Refs

- **Status:** Accepted
- **Audience:** server, web, CLI, and authorization maintainers
- **Last reviewed:** 2026-09-18

## Purpose

Protected-ref rules let dataset maintainers make selected branches and tags stricter than Niyān's installation-wide Git write policy. They govern mutation only. They do not hide commits, branches, tags, trees, or LFS objects from a user who can read the dataset.

This specification extends the baseline in [Git write transport and ref-update policy](git-write-transport.md). A configurable rule may only restrict an operation that baseline policy would otherwise allow; it can never permit force updates, default-branch deletion, in-place tag updates, unsupported ref namespaces, or an operation denied by the actor's dataset role or token.

## Rule Model

A rule belongs to one dataset and contains:

- an immutable UUID;
- a ref kind of `branch` or `tag`;
- a non-empty pattern matched against the short branch or tag name rather than the `refs/heads/` or `refs/tags/` prefix;
- a minimum role for creation and ordinary updates;
- a minimum role for deletion; and
- creation and modification timestamps.

Allowed minimum roles are `contributor`, `maintainer`, and `owner`. Tag updates remain forbidden regardless of a rule's update role. A branch update remains fast-forward-only regardless of role. The default branch remains undeletable regardless of the deletion role.

Patterns use case-sensitive shell-style matching with `*`, `?`, and character classes. A pattern without wildcard syntax matches one exact short ref name. Ref names and patterns are validated and bounded before persistence. They must not contain an explicit `refs/` prefix, traversal components, control characters, or syntax that cannot name or match a supported Git ref.

When several rules match an operation, the highest required role wins. Deleting a ref evaluates the deletion requirement; creation and fast-forward branch updates evaluate the create/update requirement. An actor must satisfy the baseline write policy, token scope and boundary, current dataset role, and every effective protected-ref restriction.

## Defaults and Management

New and existing datasets have no configurable protected-ref rules by default. This preserves ordinary Git branch behavior within Niyān's already accepted server-wide safety policy and avoids manufacturing an implicit rule for `main`.

Dataset maintainers and owners may list, create, update, and delete rules through versioned REST operations. Readers and contributors cannot read administrative rule configuration. Rule changes take effect on the next ref mutation and are enforced identically for the recommended Niyān CLI, stock Git over HTTPS, browser commits, and public mutation APIs.

A rule response includes its immutable ID, dataset ID, kind, pattern, role thresholds, and timestamps. Duplicate rules with the same dataset, kind, and pattern are rejected. Mutation uses immutable rule IDs so editing a pattern cannot accidentally target another rule.

## Enforcement

Receive-pack authorization provides the authenticated push context, and the pre-receive policy evaluates each proposed ref against the rules current at enforcement time. A push containing several updates is rejected according to the atomicity behavior in the Git write specification when any update fails protection.

Server-created browser commits and any future ref-mutation API call the same domain policy. Clients may explain a denial and show the required role when the dataset is visible, but they must not implement or cache the authoritative decision locally.

Protected-ref configuration changes and accepted or rejected protected mutations emit the audit events defined by [Audit events](audit-events.md).

## Non-goals

- Branch-level read confidentiality.
- Merge requests, approval counts, status checks, or required CI.
- Per-user exceptions, deny lists, or custom roles.
- Force-push permission for owners or administrators through product APIs.
- Protecting arbitrary Git namespaces outside branches and tags.
