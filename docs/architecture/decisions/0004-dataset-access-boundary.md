# ADR 0004: Use the Dataset as the Read-authorization Boundary

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

Path-level or confidential branch-level read rules conflict with ordinary Git fetch semantics: once a client can fetch a repository, reachable and sometimes otherwise advertised objects cannot safely be treated as isolated file-by-file secrets. Building filtered histories and object negotiation would be a substantial custom Git system.

## Decision

Authorize reads at the dataset repository boundary. A reader can browse and fetch the dataset's available history and associated LFS objects.

Niyān may protect branch and tag mutation using ordinary forge policy, including restrictions on push, force-push, deletion, or who may update selected refs. These write controls must not be described as confidential read ACLs for individual branches or paths.

## Consequences

- Authorization aligns with standard Git behavior and can be reasoned about consistently across the web, CLI, Git transport, and LFS endpoints.
- The first release does not need filtered packfiles, rewritten histories, or path-specific signed-link rules.
- Sensitive subsets must live in separate datasets with separate access grants.
- Any future finer-grained read design would require a new ADR and an explicit compatibility and migration plan.
