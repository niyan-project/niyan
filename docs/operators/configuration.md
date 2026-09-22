---
title: Configure Niyān
description: Understand Niyān's application, database, Git, and object-storage settings.
---

# Configure Niyān

Configuration is supplied through `NIYAN_` environment variables. The Compose deployment exposes the small set most installations need in `deploy/.env.example`; the server image supports additional settings documented in `apps/server/.env.example`.

## Core settings

| Setting | Purpose |
| --- | --- |
| `NIYAN_SECRET_KEY` | Long, random Django cryptographic secret. Keep it stable and private. |
| `NIYAN_DATABASE_URL` | PostgreSQL connection URL. |
| `NIYAN_GIT_ROOT` | Persistent directory containing one bare Git repository per dataset. |
| `NIYAN_GIT_HTTP_MAX_REQUEST_BYTES` | Maximum decoded size of an unknown-length Git smart-HTTP request; defaults to 4 GiB. |
| `NIYAN_ALLOWED_HOSTS` | Hostnames Django accepts. |
| `NIYAN_CSRF_TRUSTED_ORIGINS` | Trusted HTTPS origins for browser form submissions. |

In the Compose deployment, `NIYAN_PUBLIC_HOST` supplies the application hostname and is used to derive Django's host and CSRF configuration. When running the image directly, set `NIYAN_ALLOWED_HOSTS` and `NIYAN_CSRF_TRUSTED_ORIGINS` explicitly. Consult the environment templates for the exact variables accepted by the current release.

## S3-compatible storage

Configure the endpoint, region, bucket, access key, secret key, and optional object-key prefix through the `NIYAN_S3_*` settings. Niyān controls the layout below that prefix.

Clients upload, download, and stream bulk data directly to and from S3 using short-lived signed requests. Django authorizes and signs those operations but does not proxy file bodies. The object store must therefore be reachable from user machines, and its CORS and TLS configuration must match the public deployment.

Treat the bucket as private. A public bucket or public-sharing URL bypasses Niyān authorization.

## Bare Git repositories

`NIYAN_GIT_ROOT` must point at durable local or mounted filesystem storage. It is not a cache: losing it loses dataset commit graphs, branches, tags, and LFS pointer history even if the underlying S3 objects remain.

HTTP/1.1 chunked Git pushes do not carry a content length. Niyān streams those request bodies through an anonymous disk-backed temporary file before handing them to Git, capped by `NIYAN_GIT_HTTP_MAX_REQUEST_BYTES` (4 GiB by default). Ensure the application container's temporary filesystem has enough free space for the largest expected non-LFS Git pack; bulk dataset files should remain in Git LFS rather than consuming this allowance.

Do not place multiple active Niyān application installations over the same Git root unless the deployment architecture explicitly provides the required shared filesystem semantics and locking.

## Existing infrastructure

Organizations with managed PostgreSQL and S3 can run the published image directly. At minimum, provide:

- the database URL;
- a durable mounted `NIYAN_GIT_ROOT`;
- all `NIYAN_S3_*` credentials and endpoint settings;
- the public URL, allowed hosts, CSRF origins, and secret key; and
- a separately running maintenance command using the same image and configuration.

Terminate HTTPS at a trusted reverse proxy and forward the original host and scheme correctly. Niyān supports Git over HTTPS only in v1; SSH Git is not part of the deployment surface.
