# Niyān Server

## Runtime configuration

The server reads runtime configuration from environment variables. For local development, `django-environ` loads the gitignored `.env` file in this directory; operating-system environment variables take precedence. Start from `.env.example` and provide real values for the deployment.

`django-environ` provides typed environment parsing, including Django database URLs. `psycopg` is the PostgreSQL adapter required by Django for `NIYAN_DATABASE_URL` connections.

`NIYAN_GIT_ROOT` is required and selects the persistent filesystem directory containing UUID-addressed bare dataset repositories. A production installation normally uses a service-owned path such as `/var/lib/niyan/repositories` or a mounted persistent volume. Git LFS object content remains in the configured S3-compatible bucket rather than this directory.

`django-ninja` defines the versioned REST API, validates its request and response schemas, and generates its OpenAPI description so those contracts do not need to be implemented separately on top of Django.

`whitenoise` serves Django admin files and the generated Nuxt application's content-hashed assets in the initial production topology. Build `apps/web`, set `NIYAN_WEB_DIST_ROOT` when using a non-default output path, and run `uv run python manage.py collectstatic --noinput` before starting the production server. Django serves the generated SPA entry document only for routes not owned by the API, Git transport, admin, or static-file boundary.

`boto3` and `botocore` provide the standard S3 client, Signature V4 signing, custom-endpoint support, retry behavior, and presigned actions. `NIYAN_S3_*` settings select the private bucket, region, endpoint, addressing style, installation key prefix, credentials, TLS verification, and bounded client timeouts. Explicit access and secret keys must be configured together; when both are omitted, the standard AWS credential chain remains available for managed deployment identities.

`NIYAN_S3_SHA256_CHECKSUMS` defaults to `false` because checksum extensions are not uniform across S3-compatible services. Enable it only when the configured backend accepts `x-amz-checksum-sha256` on signed uploads and returns provider-validated SHA-256 metadata from `HeadObject`. Niyān then binds the Git LFS digest into uploads and requires matching metadata during finalization. With it disabled, finalization records exact-size evidence without misrepresenting it as cryptographic identity verification; Git LFS still verifies complete downloads against its SHA-256 pointer.

The object-store adapter exposes metadata, signing, multipart-control, and deletion operations only. Upload and download bodies travel directly between clients and object storage and are never accepted or proxied by Django.

The private bucket's CORS policy must allow the deployed web application's origins to perform the signed methods it uses. V1 transfer actions may require `GET`, `HEAD`, and `PUT`, the `Content-Length` and `x-amz-checksum-*` request headers, and access to provider `ETag` and checksum response headers. Command-line and server-to-server clients do not rely on browser CORS enforcement. Keep allowed origins deployment-specific rather than enabling provider public sharing.

Run `uv run python manage.py run_lfs_maintenance` as a separate long-lived server process. It uses PostgreSQL records and row locks directly; Redis and Celery are not required. `--once` performs the same bounded reconciliation pass for explicit administration and testing. The default grace period for unreferenced uploads is seven days and the default worker interval is five minutes.

The normal automated suite uses an isolated test HTTP object service and never requires public infrastructure. Its CLI acceptance cases require Git and Git LFS and use `NIYAN_TEST_DATABASE_NAME` to select a file-backed SQLite test database when hook subprocesses need to share test state. To explicitly smoke-test the configured private S3-compatible backend, run `NIYAN_RUN_S3_SMOKE_TEST=1 uv run python manage.py test datasets.tests.test_s3_smoke`. The test uploads, verifies, downloads, and deletes one tiny uniquely keyed object; it is skipped unless the opt-in variable is set.

## System dependencies

The server requires a Git installation that includes `git-http-backend`. Git initializes bare dataset repositories, serves smart-HTTP clone and fetch operations, and supplies authoritative repository metadata for the browsing API. Running the complete acceptance suite also requires Git LFS because it exercises the supported CLI workflow against the live server boundary.
