# Deploying Niyān with Docker Compose

The default deployment is a self-contained, single-node Docker Compose stack. It runs Caddy, PostgreSQL 17, SeaweedFS S3-compatible object storage, the Niyān server, and the Niyān maintenance process. The generated Nuxt SPA, Django, Gunicorn, Git, and Git LFS are contained in the versioned Niyān image.

This topology is intended for laboratories and small installations that want one supported deployment path. It is not highly available: losing the host or an unprotected volume can make the installation unavailable. Institutions with managed PostgreSQL, S3, TLS termination, or another container platform should run the same Niyān image directly with the environment variables documented under [Using existing infrastructure](#using-existing-infrastructure); there are no separate Compose variants for managed-service combinations.

Niyān v0.2 must occupy the root of its own origin, such as `https://niyan.example.org/`. Deployment beneath a URL path prefix is not supported.

## Topology

| Service | Purpose | Persistent state |
| --- | --- | --- |
| `proxy` | Caddy automatic HTTPS and routing for the application and S3 origins | `caddy-data`, `caddy-config` |
| `database` | PostgreSQL control-plane state | `postgres-data` |
| `object-storage` | SeaweedFS `weed mini` private S3-compatible data plane | `seaweed-data` |
| `server` | Django, REST, Git smart HTTP, Git LFS, admin, and the Nuxt SPA | `git-data` |
| `maintenance` | Bounded Git, upload, draft, token, and orphan reconciliation | Uses the same PostgreSQL, Git, and S3 state |
| `migrate` | Explicit one-shot Django migrations | No independent state; enabled through the `tools` profile |

Redis and Celery are not required. The maintenance process claims bounded work through PostgreSQL, runs a pass every five minutes by default, and is restarted by Docker if it exits.

Bulk Git LFS bytes never pass through Django. Caddy exposes two HTTPS origins:

- `NIYAN_PUBLIC_HOST`, such as `niyan.example.org`, routes to Django.
- `NIYAN_OBJECT_HOST`, such as `objects.niyan.example.org`, routes directly to SeaweedFS.

SeaweedFS permits browser CORS requests only from the configured Niyān application origin. Its S3, administration, filer, and volume ports are not published directly on the host.

## Prerequisites

- A Linux server with Docker Engine and the Docker Compose v2 plugin.
- A public IPv4 or IPv6 address.
- Two DNS records pointing at that server: one for Niyān and one for its object origin.
- Inbound TCP ports 80 and 443 available to Caddy. Keep PostgreSQL, SeaweedFS, and Docker's control socket unreachable from the public network.
- Outbound DNS and HTTPS access for image pulls and ACME certificate issuance.
- A filesystem suitable for durable Docker volumes. Local ext4 or XFS is a sensible single-node baseline.
- SMTP is not required in v0.2. Public registration and email invitations are not supported; the initial superuser creates installation accounts.

The benchmarked development VPS had 4 vCPUs and 8 GiB RAM. Treat that as the recommended starting point for the lab pilot, not a universal minimum. Required disk capacity depends primarily on the bare Git history in `git-data`, PostgreSQL, and the LFS bytes in `seaweed-data`. Monitor free bytes and inodes on the Docker data filesystem.

## DNS and firewall preparation

Create both DNS records before starting the proxy. For example:

```text
niyan.example.org          A/AAAA  <server address>
objects.niyan.example.org  A/AAAA  <server address>
```

Permit inbound 80/tcp and 443/tcp. Port 80 is required for ordinary ACME redirects and certificate validation even when users access only HTTPS. Restrict SSH to the institution's administration boundary. Do not expose 5432, 8333, Docker's API, or the SeaweedFS administration interfaces.

If another service already owns ports 80 or 443, stop and decide which reverse proxy will be authoritative. Do not place two independent TLS terminators on the same ports.

## First deployment

Download only the deployment bundle by executing the bootstrap from the exact release tag rather than a moving branch:

```shell
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/niyan-project/niyan/v0.2.0/deploy/install.sh | sh
cd niyan-deploy
```

The bootstrap downloads `compose.yml`, `Caddyfile`, `.env.example`, and this runbook from the same `v0.2.0` tag, then creates a mode-0600 `.env`. It refuses to overwrite a non-empty directory and does not run Docker, invoke `sudo`, or change anything outside its destination. Pass another destination with `sh -s -- /path/to/directory`. To audit before execution, download `install.sh`, inspect it, and run it locally instead of piping it directly to `sh`.

Generate URL-safe secrets. Hexadecimal values avoid quoting and database-URL encoding mistakes:

```shell
openssl rand -hex 64  # NIYAN_SECRET_KEY
openssl rand -hex 32  # NIYAN_POSTGRES_PASSWORD
openssl rand -hex 16  # NIYAN_S3_ACCESS_KEY_ID
openssl rand -hex 32  # NIYAN_S3_SECRET_ACCESS_KEY
```

Edit the gitignored `.env` and replace every example hostname, email address, and secret. Keep the PostgreSQL password hexadecimal because Compose embeds it in `NIYAN_DATABASE_URL`. Validate the rendered configuration before starting anything:

```shell
docker compose config --quiet
docker compose pull
```

Run migrations and create the first administrator. These commands start their PostgreSQL and SeaweedFS dependencies if necessary:

```shell
docker compose --profile tools run --rm migrate
docker compose --profile tools run --rm server python manage.py check --deploy
docker compose --profile tools run --rm server python manage.py createsuperuser
```

`check --deploy` may warn that HSTS subdomains and preload are disabled. That is intentional unless the operator controls every HTTPS service beneath the parent domain.

Start the complete installation:

```shell
docker compose up -d
docker compose ps
docker compose logs --tail=100 proxy server maintenance database object-storage
```

Caddy obtains certificates automatically. Initial issuance can take a short time after DNS changes become visible. Do not enable HSTS subdomains or preload merely to remove a warning.

## Acceptance checklist

Do not call the installation ready until all of these pass:

1. `docker compose ps` reports `database`, `object-storage`, and `server` healthy, with `proxy` and `maintenance` running.
2. `curl --fail https://niyan.example.org/health/live` returns success.
3. `curl --fail https://niyan.example.org/health/ready` returns success.
4. `curl --fail https://objects.niyan.example.org/status` reaches SeaweedFS through TLS. An empty successful response is valid.
5. The superuser can sign in, create a user and group, and create a dataset.
6. `niyan auth login https://niyan.example.org` completes through browser authorization.
7. A dataset containing both an ordinary Git file and an LFS-classified file can be committed and pushed.
8. A second checkout can clone the dataset and materialize the LFS file.
9. The Python client can list and stream a pinned LFS-backed path without downloading the whole file.
10. `docker compose logs maintenance` shows bounded passes without persistent failures.

Record the deployed Niyān image digest after startup:

```shell
docker image inspect ghcr.io/niyan-project/niyan:0.2.0 --format '{{index .RepoDigests 0}}'
```

## Routine operation

Useful commands:

```shell
docker compose ps
docker compose logs --since=30m server maintenance
docker compose exec database pg_isready -U niyan -d niyan
docker compose --profile tools run --rm maintenance python manage.py run_lfs_maintenance --once
```

`/health/live` proves that Django can serve HTTP. `/health/ready` also checks PostgreSQL and read/write access to the Git repository root. S3 is deliberately not part of readiness: a temporary object-store outage should not cause an otherwise healthy control-plane process to restart repeatedly.

Production logs are JSON. Collect stdout and stderr from `proxy`, `server`, `maintenance`, `database`, and `object-storage`. Alert on repeated maintenance failures, readiness failures, PostgreSQL restarts, SeaweedFS errors, certificate-renewal failures, and low bytes or inodes on the Docker volume filesystem.

The maintenance process performs one bounded pass, sleeps for `NIYAN_LFS_MAINTENANCE_INTERVAL_SECONDS`—300 seconds by default—and repeats. A pass:

- records deduplicated audit events for expired access tokens;
- removes expired open browser-commit drafts;
- reconciles Git pushes whose post-receive bookkeeping was interrupted;
- aborts expired multipart uploads;
- deletes unreferenced LFS objects after the configured seven-day grace period; and
- excludes referenced objects, active multipart sessions, and active push leases.

Object-store failures remain eligible for retry on a later pass. This is reconciliation and garbage collection, not a general-purpose job queue.

## Configuration

Compose reads the gitignored `deploy/.env`. The checked-in `.env.example` contains every required value. Never commit `.env`, paste it into an issue, or store it beside unencrypted backups.

| Variable | Meaning |
| --- | --- |
| `NIYAN_IMAGE` | Exact Niyān image tag. Prefer a complete release such as `0.2.0`; record the resolved digest. |
| `NIYAN_PUBLIC_HOST` | Application DNS hostname without scheme or path. |
| `NIYAN_OBJECT_HOST` | SeaweedFS S3 DNS hostname without scheme or path. Must differ from the application hostname. |
| `NIYAN_ACME_EMAIL` | Address used by Caddy's ACME account. |
| `NIYAN_HTTP_PORT`, `NIYAN_HTTPS_PORT` | Host ports for Caddy; normally 80 and 443. |
| `NIYAN_SECRET_KEY` | Django signing secret. Generate independently and back it up through secret management. |
| `NIYAN_POSTGRES_PASSWORD` | Password for the bundled `niyan` database role. Use generated hexadecimal text. |
| `NIYAN_S3_ACCESS_KEY_ID`, `NIYAN_S3_SECRET_ACCESS_KEY` | Private credentials shared only by Niyān and the bundled SeaweedFS service. |
| `NIYAN_S3_BUCKET` | Private bucket created by SeaweedFS at startup. |
| `NIYAN_S3_KEY_PREFIX` | Installation-owned prefix within the bucket. |
| `NIYAN_SECURE_HSTS_*` | HTTPS policy. Subdomains and preload remain explicit opt-ins. |
| `NIYAN_LOG_LEVEL` | Minimum application log severity. |

Changing a persistent-service password in `.env` does not automatically rewrite credentials already stored in a PostgreSQL or SeaweedFS volume. Treat credential rotation as an explicit maintenance operation and test it before relying on it.

## Durable state

Treat these stores as one recoverable Niyān installation:

| Store | Authoritative content | What cannot reconstruct it |
| --- | --- | --- |
| `postgres-data` | users, groups, memberships, dataset identity, authorization, tokens, audit records, and maintenance state | Git and S3 do not contain identity or access policy |
| `git-data` | bare Git repositories, commits, trees, refs, tags, and hooks | PostgreSQL indexes and S3 objects do not contain the dataset tree |
| `seaweed-data` | SeaweedFS metadata plus Git LFS object bytes and multipart state | Git LFS pointers contain identity and size, not original bytes |

`caddy-data` contains certificates and ACME state. It can be recreated when DNS and the ACME service remain available, but preserving it avoids unnecessary reissuance and rate-limit pressure. `caddy-config` contains Caddy's runtime state. Neither replaces the three authoritative data stores.

List the actual Docker volume names and mountpoints with:

```shell
docker volume inspect niyan_postgres-data niyan_git-data niyan_seaweed-data niyan_caddy-data
```

Do not manually edit files inside these volumes while their owning services are running.

## Coordinated backups

A backup that has never been restored is unverified. Provider or filesystem snapshots are preferred when they supply point-in-time consistency, encryption, retention, and separately controlled access.

1. Record the Niyān image tag and digest, release version, schema migration state, `.env` without secret values, and the bundled PostgreSQL, SeaweedFS, and Caddy image versions.
2. Stop new traffic with `docker compose stop proxy` and allow active pushes and multipart requests to finish.
3. Stop application writers with `docker compose stop server maintenance`.
4. Run one final bounded pass: `docker compose --profile tools run --rm maintenance python manage.py run_lfs_maintenance --once`.
5. Dump PostgreSQL in custom format:

   ```shell
   mkdir -p backups
   docker compose exec -T database pg_dump -U niyan -d niyan --format=custom > backups/niyan-postgres.dump
   ```

6. Stop PostgreSQL and SeaweedFS: `docker compose stop database object-storage`.
7. Snapshot `niyan_postgres-data`, `niyan_git-data`, and `niyan_seaweed-data`, or make offline archive copies using institution-approved tooling. Preserve ownership, permissions, extended attributes where applicable, and exact volume contents.
8. Record timestamps, checksums or snapshot identifiers, encryption, retention, and the responsible operator as one recovery point.
9. Restart with `docker compose up -d` and repeat the health checks.

The database dump is a portable supplement to the PostgreSQL volume snapshot, not permission to mix recovery points. Never combine PostgreSQL, Git, and SeaweedFS artifacts from unrelated times merely because each copy completed successfully.

Back up `.env`, Caddy state, and any provider credentials through the operator's secret-management and configuration-backup systems. Do not place plaintext secrets beside unencrypted data archives.

## Restore procedure

Restore into an isolated host first whenever practical.

1. Check out the exact release used by the recovery point and restore its `.env` through secret management.
2. Keep public DNS or the firewall pointed away from the recovering host.
3. Restore the matching PostgreSQL, Git, and SeaweedFS volume snapshots into empty volumes with their original names.
4. If restoring PostgreSQL from the custom dump instead of a volume snapshot, start an empty `database` service and use `pg_restore --clean --if-exists --no-owner` with an administrative role.
5. Start the exact backed-up Niyān image before attempting an upgrade.
6. Check migration state; run the `migrate` service only when intentionally upgrading.
7. Start `server` without public traffic and verify `/health/live` and `/health/ready` from inside the deployment network.
8. Validate representative private and denied workflows: browse refs, clone, fetch an LFS file, and stream a pinned file through the Python client.
9. Run `git fsck --full` against restored bare repositories using a read-only copy or maintenance window.
10. Start `maintenance`, observe a successful pass, then start `proxy` and reopen traffic.

If one authoritative store is missing, keep the installation closed. PostgreSQL cannot reconstruct Git or LFS data, Git cannot reconstruct identities or LFS bytes, and SeaweedFS cannot reconstruct paths, commits, or authorization.

## Upgrades and rollback

1. Read `CHANGELOG.md` and the GitHub release notes.
2. Change `NIYAN_IMAGE` to the complete new version; do not use an unversioned development tag.
3. Pull the image and record its digest.
4. Take and verify a coordinated backup.
5. Stop `proxy`, `server`, and `maintenance`.
6. Run the new image's migrations once with the `migrate` service.
7. Start `server`, verify health, login, repository browsing, and one LFS-backed read.
8. Start `maintenance`, then `proxy`, and monitor logs.

Do not assume an older application image can run against a newer schema. A code-only rollback is safe only when release notes explicitly say the intervening migrations and stored state are backwards-compatible. Otherwise restore the entire coordinated pre-upgrade recovery point.

PostgreSQL and SeaweedFS image upgrades are infrastructure upgrades distinct from a Niyān image update. Read their release notes and back up their volumes before changing the pinned versions. Never perform an unreviewed PostgreSQL major-version change against an existing volume.

## Using existing infrastructure

The published image is independent of Compose:

```shell
docker pull ghcr.io/niyan-project/niyan:0.2.0
```

An external deployment must run the image twice: once with its default Gunicorn command and once with `python manage.py run_lfs_maintenance`. Both instances need the same application environment and access to the same durable `NIYAN_GIT_ROOT`. Run migrations exactly once before starting the new application version.

Required application settings include:

- `NIYAN_SECRET_KEY`
- `NIYAN_ALLOWED_HOSTS`
- `NIYAN_CSRF_TRUSTED_ORIGINS`
- `NIYAN_DATABASE_URL`
- `NIYAN_GIT_ROOT`
- `NIYAN_S3_BUCKET` and the appropriate `NIYAN_S3_*` endpoint, region, credential, addressing, prefix, and TLS settings
- the HTTPS proxy and cookie settings shown in `.env.example`

PostgreSQL 17 is the tested v0.2 target. Require TLS for a database reached over an untrusted or shared network, normally by including `sslmode=require` or the institution's stronger certificate-verification policy in `NIYAN_DATABASE_URL`. Grant the application role access only to its Niyān database and schema.

For an external S3-compatible service, keep the bucket private and grant the Niyān identity only the installation bucket/prefix operations it uses: bucket listing and multipart listing, plus object read, write, delete, multipart initiation, part listing, completion, and abortion. Browser CORS must allow the exact Niyān application origin to make `GET`, `HEAD`, and `PUT` requests with signed headers and expose `ETag` and checksum response headers. Public sharing is not a substitute for signed authorization.

The reverse proxy must preserve `Authorization`, `Host`, the request method and query string, stream request and response bodies without a small upload limit, allow long Git requests, overwrite `X-Forwarded-Proto`, and route `/api/`, `/git/`, `/admin/`, `/static/`, health checks, and SPA routes to the Niyān container. The object-storage origin must route directly to the S3 service, not through Django.

## Disaster-recovery cases

- **PostgreSQL loss:** restore PostgreSQL. Creating new users or datasets against surviving Git and S3 state does not reconstruct identities, ACLs, tokens, or audit history.
- **Bare-Git loss:** restore `git-data`. LFS objects cannot reconstruct ordinary Git blobs, paths, commits, branches, or tags.
- **SeaweedFS loss:** restore `seaweed-data`. Git pointers prove expected SHA-256 and size but cannot recreate bytes.
- **Orphaned upload or interrupted push:** preserve evidence, then observe or explicitly run one maintenance pass. The worker is not a general repair tool for arbitrary backup mismatches.
- **Leaked credential:** revoke or rotate it immediately. Restoring an older database does not make an exposed access token or infrastructure credential safe.
- **Lost Django secret key:** restore it from secret backup when possible. Replacing it invalidates sessions and may invalidate other signed values, but does not replace database, Git, or S3 recovery.
- **Host loss:** provision a clean host, restore one coordinated recovery point, validate privately, then move DNS or reopen the firewall.

## Disposable recovery rehearsal

`rehearse_recovery.py` automates a destructive but isolated proof of the coordinated recovery procedure. It creates a uniquely named `niyan-recovery-rehearsal/<UUID>/` prefix in the configured S3 bucket, starts a disposable PostgreSQL 17 container, and uses temporary Git storage. It then:

1. creates and validates a recovery fixture with the tagged `v0.1.0` source;
2. backs up PostgreSQL, bare Git, and the isolated S3 prefix;
3. destroys all three source stores and restores them into an empty installation;
4. upgrades the restored installation with the current source and validates identity, authorization, Git integrity, and LFS bytes;
5. reconciles an interrupted permanent deletion; and
6. proves the documented rollback boundary by restoring the original three-store recovery point and validating it with `v0.1.0` again.

The rehearsal never reads, copies, or deletes keys outside its generated prefix. It removes both source and backup prefixes and stops the database container on success or failure. It incurs object-storage requests and requires Docker, Git, the server virtual environment, and credentials for a disposable S3-compatible bucket:

```shell
NIYAN_RUN_RECOVERY_REHEARSAL=1 apps/server/.venv/bin/python deploy/rehearse_recovery.py --env-file apps/server/.env
```

This harness verifies Niyān's recovery semantics; it is not a production backup command. Operators remain responsible for snapshot tooling, encryption, retention, restoration drills, and named personnel appropriate to their institution.
