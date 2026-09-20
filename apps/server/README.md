# Niyān Server

[![Server coverage](https://codecov.io/github/niyan-project/niyan/graph/badge.svg?flag=server)](https://app.codecov.io/github/niyan-project/niyan)
[![Python 3.13](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Django 5.2 LTS](https://img.shields.io/badge/Django-5.2_LTS-092E20?logo=django&logoColor=white)](https://www.djangoproject.com/)

## Runtime configuration

The server reads runtime configuration from environment variables. For local development, `django-environ` loads the gitignored `.env` file in this directory; operating-system environment variables take precedence. Start from `.env.example` and provide real values for the deployment.

Production uses Gunicorn and defaults to HTTPS-only behavior when `NIYAN_DEBUG=false`: secure session and CSRF cookies, redirect-to-HTTPS, and a one-year HSTS lifetime. Set `NIYAN_BEHIND_HTTPS_PROXY=true` only when a trusted reverse proxy overwrites `X-Forwarded-Proto`; Django then uses that header to recognize the original HTTPS request. `NIYAN_CSRF_TRUSTED_ORIGINS` contains complete trusted origins, while `NIYAN_ALLOWED_HOSTS` contains host names. HSTS subdomains and preload remain explicit opt-ins because enabling either without controlling the entire domain can make other services unreachable.

Production logs are single-line JSON by default and contain timestamp, severity, logger, message, optional HTTP status, and exception text. The formatter deliberately does not serialize request headers, bodies, cookies, tokens, or arbitrary record attributes. Set `NIYAN_LOG_FORMAT=console` for human-readable local output and use `NIYAN_LOG_LEVEL` to select the minimum severity.

`/health/live` checks only that the process can serve HTTP. `/health/ready` checks PostgreSQL and the readable, writable `NIYAN_GIT_ROOT`; neither response includes backend exceptions or credentials. S3 is intentionally absent from readiness so a temporary object-store outage does not remove otherwise healthy control-plane instances from service. Run `python manage.py check --deploy` with production environment values before an upgrade. Its warnings about HSTS subdomains and preload are expected unless those deployment-wide choices were explicitly enabled.

`django-environ` provides typed environment parsing, including Django database URLs. `psycopg` is the PostgreSQL adapter required by Django for `NIYAN_DATABASE_URL` connections. PostgreSQL is the only supported control-plane database; settings loading rejects SQLite and other database engines so development and tests cannot silently exercise weaker or different transaction and concurrency semantics.

`NIYAN_GIT_ROOT` is required and selects the persistent filesystem directory containing UUID-addressed bare dataset repositories. A production installation normally uses a service-owned path such as `/var/lib/niyan/repositories` or a mounted persistent volume. Git LFS object content remains in the configured S3-compatible bucket rather than this directory.

`django-ninja` defines the versioned REST API, validates its request and response schemas, and generates its OpenAPI description so those contracts do not need to be implemented separately on top of Django.

`whitenoise` serves Django admin files and the generated Nuxt application's content-hashed assets in the initial production topology. Build `apps/web`, set `NIYAN_WEB_DIST_ROOT` when using a non-default output path, and run `uv run python manage.py collectstatic --noinput` before starting the production server. Django serves the generated SPA entry document only for routes not owned by the API, Git transport, admin, or static-file boundary.

`boto3` and `botocore` provide the standard S3 client, Signature V4 signing, custom-endpoint support, retry behavior, and presigned actions. `NIYAN_S3_*` settings select the private bucket, region, endpoint, addressing style, installation key prefix, credentials, TLS verification, and bounded client timeouts. Explicit access and secret keys must be configured together; when both are omitted, the standard AWS credential chain remains available for managed deployment identities.

`NIYAN_S3_SHA256_CHECKSUMS` defaults to `false` because checksum extensions are not uniform across S3-compatible services. Enable it only when the configured backend accepts `x-amz-checksum-sha256` on signed uploads and returns provider-validated SHA-256 metadata from `HeadObject`. Niyān then binds the Git LFS digest into uploads and requires matching metadata during finalization. With it disabled, finalization records exact-size evidence without misrepresenting it as cryptographic identity verification; Git LFS still verifies complete downloads against its SHA-256 pointer.

The object-store adapter exposes metadata, signing, multipart-control, and deletion operations only. Upload and download bodies travel directly between clients and object storage and are never accepted or proxied by Django.

The private bucket's CORS policy must allow the deployed web application's origins to perform the signed methods it uses. V1 transfer actions may require `GET`, `HEAD`, and `PUT`, the `Content-Length` and `x-amz-checksum-*` request headers, and access to provider `ETag` and checksum response headers. Command-line and server-to-server clients do not rely on browser CORS enforcement. Keep allowed origins deployment-specific rather than enabling provider public sharing.

Run `uv run python manage.py run_lfs_maintenance` as a separate long-lived server process. It reconciles missed Git post-receive work, expired browser drafts and access tokens, multipart uploads, and unreferenced Git LFS objects. It uses PostgreSQL records and row locks directly; Redis and Celery are not required. `--once` performs the same bounded reconciliation pass for explicit administration and testing. The default grace period for unreferenced uploads is seven days and the default worker interval is five minutes.

Run the complete server suite from the repository root with `./apps/server/test`. The command starts an ephemeral PostgreSQL 17 container, waits for readiness, checks for missing migrations, runs every Django and cross-component test with branch coverage, enforces the 85% server coverage floor, writes XML and HTML reports, and removes the database container afterward. Git and Git LFS must be installed on the host because the acceptance cases exercise both through subprocesses. Set `NIYAN_TEST_POSTGRES_PORT` only when the default loopback port `55432` is unavailable. Pass Django test labels after the command to run a narrower selection against the same PostgreSQL environment.

The normal automated suite uses an isolated test HTTP object service and never requires public infrastructure. Hook subprocesses connect to the same PostgreSQL test database as Django, so their authorization and transaction behavior is exercised without a database-specific workaround. To explicitly smoke-test the configured private S3-compatible backend, run `NIYAN_RUN_S3_SMOKE_TEST=1 uv run python manage.py test datasets.tests.test_s3_smoke` with a PostgreSQL `NIYAN_DATABASE_URL`. The test uploads, verifies, downloads, and deletes one tiny uniquely keyed object; it is skipped unless the opt-in variable is set.

## System dependencies

The server requires a Git installation that includes `git-http-backend`. Git initializes bare dataset repositories, serves smart-HTTP clone and fetch operations, and supplies authoritative repository metadata for the browsing API. Running the complete acceptance suite also requires Git LFS because it exercises the supported CLI workflow against the live server boundary.
