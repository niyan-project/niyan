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

## Authoritative state

Treat these three stores as one recoverable Niyān installation:

| Store | Authoritative content | What cannot reconstruct it |
| --- | --- | --- |
| PostgreSQL | users, groups, memberships, dataset identity, authorization, tokens, audit records, transfer and maintenance state | Git and S3 do not contain identity or access policy |
| `NIYAN_GIT_ROOT` | bare Git repositories, commits, trees, refs, tags, and server hooks | PostgreSQL indexes and S3 objects do not contain the dataset tree |
| S3-compatible bucket and `NIYAN_S3_KEY_PREFIX` | Git LFS object bytes and multipart data-plane state | a Git LFS pointer records identity and size, not the original bytes |

Losing any one store can make some or all datasets unavailable. PostgreSQL metadata derived from Git can be rebuilt in principle, but v1 does not ship a complete reconstruction command. Back up all three.

## Coordinated backups

Provider snapshots are preferred when they offer point-in-time consistency, encryption, retention, and independent access controls. A backup that has never been restored is unverified.

1. Record the running image tag or digest, release version, schema migration state, and non-secret deployment configuration.
2. Stop new traffic at the reverse proxy and allow active pushes, browser commits, and multipart requests to finish.
3. Stop the long-running services with `docker compose stop server maintenance`.
4. Run one final bounded reconciliation pass with `docker compose run --rm maintenance python manage.py run_lfs_maintenance --once`, then ensure no Niyān process can write to any store.
5. Create a PostgreSQL snapshot or a custom-format `pg_dump` using administrative tooling outside the Niyān container.
6. Snapshot or copy the complete `git-data` volume, preserving repository contents, hooks, permissions, and names exactly.
7. Snapshot or copy the complete configured S3 key prefix, including object metadata. Provider versioning is useful but is not a substitute for a separately protected backup.
8. Record the identifiers, timestamps, checksums or provider manifests, and retention policy for all three artifacts as one recovery point.
9. Restart with `docker compose up -d server maintenance` and confirm `/health/ready` succeeds through the proxy.

Do not take the three copies while writes continue and call them coordinated. A Git ref may otherwise point at an LFS object not present in the object-store snapshot, or PostgreSQL may describe a repository state outside the Git snapshot. If downtime is unacceptable, use storage-native coordinated snapshot facilities and validate their ordering against real push and multipart traffic before relying on them.

Static access keys, database credentials, Django's secret key, TLS private keys, and reverse-proxy configuration must be backed up through the operator's secret-management system rather than placed beside unencrypted data copies. A restored database containing token hashes does not reveal token secrets, but it is still sensitive authorization data.

## Restore procedure

Restore into an isolated environment first whenever practical.

1. Keep the reverse proxy closed and stop `server` and `maintenance`.
2. Select one coordinated recovery point and the exact Niyān image that created it.
3. Restore the S3 bucket or key prefix without enabling public sharing.
4. Restore the complete bare-Git volume at `/var/lib/niyan/repositories` and make it readable and writable by container UID and GID `10001`.
5. Restore PostgreSQL into an empty database using an administrative role, then grant the application role only the privileges needed to connect and operate that database.
6. Restore secrets and non-secret configuration, start the exact backed-up image, and check its migration state before applying a newer release.
7. Run `docker compose --profile tools run --rm migrate` only when intentionally upgrading from the restored version.
8. Start `server` without external traffic, verify `/health/live` and `/health/ready`, and inspect logs for bounded errors.
9. Validate representative private and authorized-denial workflows: browse refs and history, clone a dataset, fetch an LFS-backed file, and stream a pinned file through the Python client.
10. Run `git fsck --full` against restored bare repositories using a read-only copy or a maintenance window. Compare the restored S3 inventory with the backup manifest and investigate missing referenced objects before reopening traffic.
11. Start `maintenance`, observe one reconciliation pass, then reopen the reverse proxy.

Never combine PostgreSQL from one recovery point with Git or S3 from another merely because each restore completed successfully. If that is the only available option, keep the installation closed, expect orphaned or missing state, and perform manual reconciliation before users can access it.

## Upgrades

1. Read `CHANGELOG.md` and release notes for schema, configuration, storage, and compatibility changes.
2. Build or pull an immutable image and verify its provenance before the maintenance window.
3. Take and verify a coordinated backup as described above.
4. Stop traffic, `server`, and `maintenance`.
5. Run the new image's migrations once through the `migrate` service.
6. Start `server`, wait for readiness, exercise login and a representative read-only dataset workflow, then start `maintenance`.
7. Reopen traffic and monitor structured logs, database connections, repository storage, and object-store errors.

Do not run migrations concurrently from every application replica. Do not assume an older application image can run against a newer schema. A code rollback is safe only when the release notes explicitly say the intervening migrations and stored state are backwards-compatible. Otherwise, restore the entire coordinated pre-upgrade recovery point. Reverse migrations may be unavailable or may discard data.

## Disaster-recovery cases

- **PostgreSQL loss:** keep the service closed and restore PostgreSQL. Creating new users or datasets against surviving Git and S3 state will not reconstruct original identities, ACLs, tokens, or audit history.
- **Bare-Git loss:** restore the complete repository volume. S3 LFS objects cannot reconstruct file paths, commits, branches, tags, or ordinary Git blobs.
- **S3 object loss:** restore the object-store recovery point. The Git pointer proves the expected SHA-256 and size but cannot recreate the bytes.
- **Orphaned upload or interrupted push:** preserve evidence first, then run one maintenance pass. The worker safely handles its specified expired drafts, multipart sessions, push reconciliation, tokens, and unreferenced-upload grace period; it is not a general repair tool for arbitrary backup mismatches.
- **Leaked credential:** revoke or rotate it immediately. Database restore does not make a previously exposed access token or infrastructure credential safe again.
- **Lost Django secret key:** restore it from secret backup when possible. Replacing it invalidates sessions and may invalidate other Django-signed values, but it does not replace database, Git, or S3 recovery.

Test recovery on a schedule appropriate to the installation. At minimum, measure how long it takes to restore all three stores, validate an LFS-backed dataset end to end, and rotate temporary recovery credentials. Document provider-specific commands and responsible operators outside this repository; Niyān's guide cannot know an institution's snapshot, retention, or network controls.
