# Niyān Specifications

The specifications describe what Niyān is intended to do. They live outside `AGENTS.md` so that product and protocol detail can evolve without turning the always-loaded agent instructions into a large design document.

## Status

Every specification starts with one of these states:

- **Draft**: under discussion; implementation must not assume it is final.
- **Accepted**: approved as the intended behavior.
- **Superseded**: retained for history and linked to its replacement.

Architecture decision records use corresponding `Proposed`, `Accepted`, `Deprecated`, and `Superseded` states.

## Current Documents

- [System overview](system-overview.md) — the initial product boundary, system model, workflows, and open questions.
- [Authentication](authentication.md) — browser sessions, CLI login, access tokens, scopes, and credential handling.
- [Authorization](authorization.md) — namespace roles, dataset grants, permission inheritance, and the shared policy boundary.
- [Repository access](repository-access.md) — read-only Git-over-HTTPS transport and repository browsing APIs.
- [Git write transport and ref-update policy](git-write-transport.md) — authenticated receive-pack, initial branch and tag rules, concurrency, hooks, and LFS publication checks.
- [Git LFS and object-storage protocol](git-lfs-and-object-storage.md) — large-object identity, direct and multipart transfers, verification, retries, and failed-push cleanup.
- [CLI Git LFS tracking policy](cli-lfs-tracking.md) — deterministic binary and size classification, attribute precedence, overrides, and directory traversal.
- [CLI](cli.md) — the command surface and behavior of the `niyan` console entry point.
- [Namespaces and datasets](namespaces-and-datasets.md) — stable identity, human-facing paths, and dataset repository creation.
- [Python filesystem client](python-client.md) — the unified package's read-only `fsspec` surface for streaming and downloads.
- [Web application](web-application.md) — the Nuxt SPA, same-origin deployment, visual system, browser workflows, and Phase 3 acceptance boundary.

The system overview, authentication, authorization, namespace and dataset lifecycle, repository-read contract, Git write policy, Git LFS protocol, Git LFS tracking policy, CLI, and web application are accepted. The Python filesystem client remains draft pending its addressing and compatibility decision, which its draft boundary states explicitly.

As the design becomes concrete, split the overview into focused specifications for the remaining storage lifecycle, REST API, viewer plugins, and deployment behavior. Do not create those documents merely to fill out a directory; create each when it has enough substance to review.

## Writing a Specification

A specification should state:

1. Its status and intended audience.
2. The problem and goals.
3. Normative behavior, using **must**, **should**, and **may** deliberately.
4. Failure behavior and security boundaries.
5. Compatibility or migration constraints.
6. Explicit non-goals and unresolved questions.

Keep rationale brief and link to the ADR that records a significant architectural tradeoff. Specifications define behavior; ADRs explain why a durable choice was made.
