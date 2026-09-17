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
- [Namespaces and datasets](namespaces-and-datasets.md) — stable identity, human-facing paths, and dataset repository creation.
- [Python filesystem client](python-client.md) — the read-only PyPI package for `fsspec` streaming and downloads.

As the design becomes concrete, split the overview into focused specifications for the dataset model, Git LFS protocol, storage lifecycle, authorization, CLI, REST API, viewer plugins, and deployment. Do not create those documents merely to fill out a directory; create each when it has enough substance to review.

## Writing a Specification

A specification should state:

1. Its status and intended audience.
2. The problem and goals.
3. Normative behavior, using **must**, **should**, and **may** deliberately.
4. Failure behavior and security boundaries.
5. Compatibility or migration constraints.
6. Explicit non-goals and unresolved questions.

Keep rationale brief and link to the ADR that records a significant architectural tradeoff. Specifications define behavior; ADRs explain why a durable choice was made.
