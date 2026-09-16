# Architecture Decision Records

Architecture decision records (ADRs) capture consequential choices, their context, and their tradeoffs. They complement the specifications: an ADR explains why; a specification defines observable behavior.

## Status

- **Proposed**: drafted for review and not yet binding.
- **Accepted**: approved and expected to guide implementation.
- **Deprecated**: still present but discouraged.
- **Superseded**: replaced by a later ADR, which must be linked.

## Index

- [ADR 0001: Use a modular monorepo](0001-modular-monorepo.md) — Accepted
- [ADR 0002: Model datasets as Git repositories](0002-git-native-datasets.md) — Accepted
- [ADR 0003: Use Git LFS with an S3-compatible data plane](0003-git-lfs-s3-data-plane.md) — Accepted
- [ADR 0004: Use the dataset as the read-authorization boundary](0004-dataset-access-boundary.md) — Accepted
- [ADR 0005: Transfer large objects directly rather than through Django](0005-direct-object-transfers.md) — Accepted
- [ADR 0006: Store bare repositories on a persistent filesystem and serve them with Git](0006-git-repository-hosting.md) — Accepted
- [ADR 0007: Use one S3-compatible bucket with dataset-scoped keys](0007-shared-s3-bucket.md) — Accepted
- [ADR 0008: Use Python 3.13 and Django 5.2 LTS](0008-backend-baseline.md) — Accepted

## Creating an ADR

Copy this shape into the next zero-padded file:

```markdown
# ADR NNNN: Decision title

- **Status:** Proposed
- **Date:** YYYY-MM-DD

## Context

What forces and constraints require a decision?

## Decision

What are we choosing?

## Consequences

What becomes easier, harder, or deliberately deferred?
```

Do not rewrite an accepted ADR to conceal a changed decision. Supersede it with a new record.
