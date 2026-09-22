---
title: Running Niyān
description: Deploy and operate a self-hosted Niyān installation.
---

# Running Niyān

This guide is for IT staff and Niyān administrators. It covers the supported single-node Docker Compose deployment as well as the settings needed to run the Niyān image with existing infrastructure.

## Choose a deployment model

### Self-contained Docker Compose

Use the repository's Compose stack for the simplest supported installation. It runs:

- Caddy for HTTPS and routing;
- the Niyān Django application and static web interface;
- PostgreSQL 17;
- SeaweedFS with an S3-compatible gateway; and
- the Niyān maintenance process.

PostgreSQL, Git repositories, and object data use separate persistent Docker volumes.

### Existing infrastructure

Run `ghcr.io/niyan-project/niyan:<version>` directly when your organization already provides PostgreSQL, S3-compatible storage, persistent filesystem storage for bare Git repositories, and an HTTPS reverse proxy. The same image serves the application and can run migrations, administrative commands, or maintenance.

## Operator path

1. [Deploy with Docker Compose](/operators/deployment).
2. Create the required first superuser during deployment; there is no public registration.
3. [Review configuration and storage boundaries](/operators/configuration).
4. [Create users, groups, and access rules](/operators/administration).
5. [Monitor routine maintenance](/operators/maintenance).
6. [Establish and rehearse backups](/operators/backup-and-restore).
7. [Follow the upgrade procedure](/operators/upgrades).

::: warning Pre-v1 software
Niyān is currently in the `0.x` release line. Pin an exact container tag, read release notes before upgrading, and rehearse restore procedures before putting irreplaceable data into service.
:::
