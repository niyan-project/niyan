---
title: Upgrade
description: Upgrade a Niyān installation safely between pinned releases.
---

# Upgrade

Niyān publishes versioned container images to GitHub Container Registry. Always deploy an exact semantic version such as `0.3.0`; do not base production on `latest`.

## Before upgrading

1. Read the release notes and changelog for every skipped version.
2. Confirm client compatibility and any required environment-variable changes.
3. Take a coordinated backup of PostgreSQL, Git storage, and S3 objects.
4. Verify that backup by restoring it in an isolated environment when the change is material.
5. Record the currently deployed image tag for rollback planning.

## Upgrade the Compose deployment

Update `NIYAN_IMAGE` in `deploy/.env`, then:

```shell
docker compose pull
docker compose --profile tools run --rm migrate
docker compose --profile tools run --rm server python manage.py check --deploy
docker compose up -d
docker compose ps
```

Inspect both application and maintenance logs after startup. Repeat the acceptance checks for authentication, dataset browsing, Git transport, Git LFS, and fsspec.

## Rollback limits

Rolling the image tag backward is not necessarily a safe rollback after database migrations have run. Django migrations may change data or schemas in ways older code cannot understand. Use the release notes to determine compatibility; when uncertain, restore the complete coordinated pre-upgrade backup instead.

Never restore only the database while leaving Git and S3 at a later point in time unless you have explicitly verified that the resulting state is consistent.

## Client releases

The `niyan` Python package follows its own published version. Operators should communicate supported client versions to users and upgrade a representative CLI/fsspec environment during acceptance testing before organization-wide rollout.
