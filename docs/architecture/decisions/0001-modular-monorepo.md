# ADR 0001: Use a Modular Monorepo

- **Status:** Proposed
- **Date:** 2026-09-16

## Context

Niyān's Django server, Nuxt web application, standalone CLI, PyPI Python client, viewer SDK, shared contracts, deployment configuration, and end-to-end tests will evolve together. The public API still needs real boundaries, but splitting these pieces into separate repositories immediately would add release coordination and make cross-component changes harder before those boundaries have stabilized.

## Decision

Keep the first-party Niyān components in one repository, organized as independently testable and releasable modules. The intended top-level structure is recorded in the root `AGENTS.md`.

The web application, CLI, and Python client must consume documented server interfaces rather than import server implementation code. Generated or shared contracts may cross boundaries only through an explicit package or artifact. The CLI and Python client are independently distributed products and must not depend on one another at runtime.

## Consequences

- One change can update the API, clients, documentation, and end-to-end tests atomically.
- Contributors have one issue tracker, development checkout, and review surface.
- CI can select affected components while retaining an integrated test suite.
- Repository tooling must avoid forcing every contributor to install every language tool for a component they are not changing.
- Components may move to separate repositories later if independent governance or release cadence outweighs coordination cost.
