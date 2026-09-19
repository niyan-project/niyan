# Deploying Niyān with Docker Compose

The production image contains Django, the generated Nuxt SPA, Gunicorn, Git, Git LFS, and the Niyān maintenance command. PostgreSQL and S3-compatible object storage are deliberately external services: Niyān does not require operators to replace institutional storage with bundled containers.

## Prerequisites

- Docker Engine with the Compose plugin
- PostgreSQL reachable from the containers
- A private S3-compatible bucket and credentials, workload identity, or instance role
- An HTTPS reverse proxy that preserves `/api/`, `/git/`, `/admin/`, `/static/`, and application routes
- Durable local or network-backed storage for the `git-data` Docker volume

Niyān v1 must occupy the root of its own origin, such as `https://niyan.example.org/`. Deployment below a path prefix is not supported.

## First deployment

From this directory:

```shell
cp .env.example .env
# Edit .env with production values.
docker compose build
docker compose --profile tools run --rm migrate
docker compose --profile tools run --rm server python manage.py createsuperuser
docker compose up -d server maintenance
docker compose ps
```

Compose binds Gunicorn to `127.0.0.1:8000` by default so a host reverse proxy can terminate TLS. Change `NIYAN_BIND_ADDRESS` only when the surrounding network boundary requires it. Forward the original scheme and overwrite `X-Forwarded-Proto`; the default configuration trusts that header because `NIYAN_BEHIND_HTTPS_PROXY=true`.

The application does not apply migrations automatically when it starts. This makes schema changes an explicit, observable deployment step and prevents multiple replicas from racing migrations.

## Configuration and credentials

Docker Compose reads the gitignored `deploy/.env`. The checked-in `.env.example` contains every required deployment value and safe security defaults. Never commit the populated file.

Explicit S3 access keys are optional. If `NIYAN_S3_ACCESS_KEY_ID` and `NIYAN_S3_SECRET_ACCESS_KEY` are absent, boto3 uses its standard workload credential chain. Both variables must be provided together when static credentials are used. Public bucket sharing must remain disabled; Niyān authorizes each operation before issuing a short-lived signed action.

`NIYAN_GIT_ROOT` is fixed to `/var/lib/niyan/repositories` inside the image and is backed by the `git-data` volume. This volume is authoritative data and must be included in backups. Git LFS content remains in object storage.

## Services

- `server` runs Gunicorn and serves the REST API, Git smart HTTP, admin, generated SPA, and static assets.
- `maintenance` runs bounded reconciliation for Git pushes, browser drafts, access tokens, multipart uploads, and unreferenced LFS objects.
- `migrate` is an on-demand tool profile rather than a long-running service.

The server health check calls `/health/ready`. Use `/health/live` for process supervision that must not restart an instance merely because PostgreSQL is recovering.

## Building elsewhere

The same root `Dockerfile` works without Compose:

```shell
docker build --tag niyan:local ..
```

The build is multi-stage. Node and pnpm exist only in the web builder; the final non-root image contains the Python runtime and required Git transport tools. The Nuxt entry document and collected content-hashed static assets are baked into the final image.
