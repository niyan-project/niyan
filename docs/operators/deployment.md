---
title: Deploy with Docker Compose
description: Launch a self-contained Niyān server with Docker Compose.
---

# Deploy with Docker Compose

The supported Compose deployment is intended for a single host. It includes every required service except DNS and SMTP.

## Prerequisites

Provide a Linux server with:

- Docker Engine and the Docker Compose plugin;
- persistent disk space sized for PostgreSQL, bare Git repositories, and dataset objects;
- two DNS names pointing to the host, such as `data.example.edu` and `objects.data.example.edu`; and
- inbound TCP ports 80 and 443.

Do not expose PostgreSQL port 5432 or SeaweedFS S3 port 8333 to the public network.

Niyān must currently be installed at the root of an origin such as `https://data.example.edu`. Hosting it beneath a path such as `https://example.edu/niyan` is not supported.

## Get the pinned deployment bundle

The published container contains the Niyān application. The self-contained installation additionally needs the Compose, Caddy, and environment-template files that orchestrate Niyān with PostgreSQL and SeaweedFS.

Run the bootstrap script from the exact release tag you intend to deploy:

```shell
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/niyan-project/niyan/v0.2.0/deploy/install.sh | sh
cd niyan-deploy
```

The script downloads only `compose.yml`, `Caddyfile`, `.env.example`, and the deployment runbook from the same `v0.2.0` tag, then creates a private `.env` for editing. It refuses to overwrite a non-empty destination and does not invoke `sudo`, start Docker, or modify the host outside `niyan-deploy`.

To choose a different empty destination, pass it after `sh -s --`:

```shell
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/niyan-project/niyan/v0.2.0/deploy/install.sh | sh -s -- /srv/niyan
cd /srv/niyan
```

Pinning the URL matters: do not pipe the moving `main` branch into a shell. Operators who do not permit piped installers can download and inspect `install.sh` first, or manually download the four files listed above.

Edit `.env` and set the public hostnames, ACME email address, and generated secrets. Generate independent values with:

```shell
openssl rand -hex 64
openssl rand -hex 32
openssl rand -hex 16
```

Use them for `NIYAN_SECRET_KEY`, `NIYAN_POSTGRES_PASSWORD`, and the S3 credentials respectively. Never commit `.env`.

Validate the rendered configuration and pull the pinned image:

```shell
docker compose config --quiet
docker compose pull
```

## Initialize the system

Apply the database migrations and run Django's production checks:

```shell
docker compose --profile tools run --rm migrate
docker compose --profile tools run --rm server python manage.py check --deploy
```

## Create the first system administrator

Niyān has no public registration and a fresh installation contains no usable accounts. Before starting normal operation, create the first Django superuser:

```shell
docker compose --profile tools run --rm server python manage.py createsuperuser
```

The command prompts for a username, email address, and password. This account has unrestricted access to the Niyān dashboard, the **System** administration pages, and Django admin. Protect it accordingly and use it to create the ordinary staff and user accounts needed by the installation.

::: danger Do not skip this step
Without the first superuser, nobody can sign in to bootstrap users, groups, or permissions. Niyān intentionally has no public sign-up flow.
:::

Start the complete service stack:

```shell
docker compose up -d
```

Open the configured Niyān URL and sign in with the superuser account. The SeaweedFS object hostname is an implementation endpoint and should not be presented as the application URL.

## Acceptance checks

Before inviting users:

1. Confirm every service is healthy with `docker compose ps`.
2. Sign in through the web application with the superuser created during initialization.
3. Create a group and a test user under **System**.
4. Install the CLI on another machine and run `niyan auth login https://<public-host>`.
5. Create, clone, push, and pull a small test dataset containing an LFS file.
6. Stream that file through fsspec.
7. Run and record a backup and restore rehearsal.

The repository also contains a more command-oriented [deployment runbook](https://github.com/niyan-project/niyan/blob/main/deploy/README.md).
