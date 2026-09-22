---
title: Back up and restore
description: Protect and recover PostgreSQL metadata, bare Git repositories, and dataset objects.
---

# Back up and restore

A usable Niyān backup has three coordinated parts:

1. PostgreSQL metadata, identities, permissions, and LFS upload records;
2. the complete `NIYAN_GIT_ROOT` containing bare repositories; and
3. the S3 bucket or SeaweedFS volume containing LFS objects.

Backing up only one or two parts is not a complete recovery strategy.

## Backup principles

- Use a PostgreSQL-native logical or physical backup appropriate to your recovery objectives.
- Snapshot or archive Git storage without omitting repository files, hooks, or configuration.
- Use your S3 provider's versioning, replication, or backup facility; for Compose, protect the complete SeaweedFS volume.
- Record the Niyān image tag and deployment configuration used with every backup set.
- Protect backups as strongly as production: they contain private dataset content and authentication metadata.

For the tightest consistency, briefly stop write traffic and the maintenance service while capturing the three stores. If the storage platforms provide coordinated point-in-time snapshots, document and test their ordering guarantees.

## Restore order

1. Stop Niyān application and maintenance processes.
2. Restore PostgreSQL.
3. Restore the Git root to the exact configured path and ownership.
4. Restore S3 or SeaweedFS objects under the same bucket and key prefix.
5. Restore configuration, including the stable `NIYAN_SECRET_KEY` where required.
6. Start the application, run Django system checks, and then start maintenance.
7. Verify web login, permissions, Git clone/fetch, Git LFS download, and fsspec ranged reads.

Do not run migrations from a newer release against a restored older database until you have decided to complete the corresponding application upgrade.

## Rehearse recovery

A backup is not proven until it has been restored into an isolated environment. Rehearse using different hostnames and isolated credentials so the test system cannot sign requests against or mutate production storage. Record recovery time and any manual steps, then update the runbook.
