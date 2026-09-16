# ADR 0008: Use Python 3.13 and Django 5.2 LTS

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

The control plane needs a stable Python and Django baseline before application scaffolding and dependency selection can become reproducible. Self-hosted research infrastructure benefits more from a long security-support window than from adopting every Django feature release immediately.

## Decision

Use Python 3.13 for the initial backend environment and Django 5.2 LTS for the control plane. Manage the server environment and lockfile with uv. Track current patch releases within the Django 5.2 series and review framework upgrades deliberately rather than drifting across feature releases.

This decision applies to the Django server. The independently published Python filesystem client may support a broader Python version range once its packaging requirements are specified.

## Consequences

- Contributors and deployment automation share one documented backend runtime baseline.
- Django 5.2's LTS window suits long-lived self-hosted installations.
- Dependencies must remain compatible with Python 3.13 and Django 5.2.
- Supporting an additional Python or Django series requires explicit testing rather than an unconstrained version declaration.
- A future LTS upgrade should be handled as a planned migration with deprecation warnings resolved before the version change.
