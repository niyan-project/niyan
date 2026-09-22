---
title: Routine maintenance
description: Monitor services, logs, storage, and Niyān's maintenance process.
---

# Routine maintenance

The Compose stack runs a dedicated maintenance service using the same Niyān image. It starts the long-running `run_lfs_maintenance` Django management command, which wakes on a regular interval and performs bounded maintenance work.

## What the maintenance process does

Git LFS uploads can reach S3 before the corresponding Git push succeeds. Niyān records upload sessions in PostgreSQL. The maintenance process reconciles stale sessions and makes abandoned, unreferenced objects eligible for deletion after the configured grace period.

It coordinates through PostgreSQL records and row locks. Redis and Celery are not required. Bulk transfers are never processed by the maintenance worker; clients communicate directly with S3.

## Check service health

```shell
docker compose ps
docker compose logs --tail=200 server
docker compose logs --tail=200 maintenance
```

The application, maintenance process, PostgreSQL, and SeaweedFS should all remain healthy. Investigate repeated maintenance exceptions rather than assuming cleanup will catch up indefinitely.

## Routine operator checks

- Monitor free space for all three durable stores: PostgreSQL, Git, and object storage.
- Monitor certificate renewal and both public HTTPS hostnames.
- Review authentication failures and unexpected administrative actions in logs and audit events.
- Confirm the maintenance service is continuously running.
- Run restore rehearsals on a schedule, not only after an incident.
- Upgrade from pinned images deliberately rather than following a mutable tag.

## Change the interval or grace period

The environment template exposes maintenance cadence, reconciliation batch sizes, and abandoned-upload grace settings. Longer grace periods consume more object storage but reduce the chance of reclaiming data associated with a delayed workflow. Keep batches bounded so cleanup does not monopolize the database or object store.

Restart the maintenance service after changing its configuration:

```shell
docker compose up -d --force-recreate maintenance
```
