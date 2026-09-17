# Niyān Server

## Runtime configuration

The server reads runtime configuration from environment variables. For local development, `django-environ` loads the gitignored `.env` file in this directory; operating-system environment variables take precedence. Start from `.env.example` and provide real values for the deployment.

`django-environ` provides typed environment parsing, including Django database URLs. `psycopg` is the PostgreSQL adapter required by Django for `NIYAN_DATABASE_URL` connections.

`django-ninja` defines the versioned REST API, validates its request and response schemas, and generates its OpenAPI description so those contracts do not need to be implemented separately on top of Django.

`boto3` and `botocore` provide the standard S3 client, Signature V4 signing, custom-endpoint support, retry behavior, and presigned actions. `NIYAN_S3_*` settings select the private bucket, region, endpoint, addressing style, installation key prefix, credentials, TLS verification, and bounded client timeouts. Explicit access and secret keys must be configured together; when both are omitted, the standard AWS credential chain remains available for managed deployment identities.

The object-store adapter exposes metadata, signing, multipart-control, and deletion operations only. Upload and download bodies travel directly between clients and object storage and are never accepted or proxied by Django.

The private bucket's CORS policy must allow the deployed web application's origins to perform the signed methods it uses. V1 transfer actions may require `GET`, `HEAD`, and `PUT`, the `Content-Length` and `x-amz-checksum-*` request headers, and access to provider `ETag` and checksum response headers. Command-line and server-to-server clients do not rely on browser CORS enforcement. Keep allowed origins deployment-specific rather than enabling provider public sharing.

## System dependencies

The server requires a Git installation that includes `git-http-backend`. Git initializes bare dataset repositories, serves smart-HTTP clone and fetch operations, and supplies authoritative repository metadata for the browsing API.
