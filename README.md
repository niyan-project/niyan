# Niyān

Niyān is an open-source, self-hosted data forge for researchers and machine-learning teams. Each dataset is an ordinary Git repository; Git owns commits, trees, branches, tags, and merges, while standard Git LFS stores large content in private S3-compatible object storage. Django provides identity, authorization, policy, and a public REST API, and Nuxt provides the web application.

The `niyan` Python package combines the recommended command-line experience with a read-only `fsspec` filesystem for checkout-free streaming on workstations and HPC systems. Standard Git and Git LFS over HTTPS remain supported for users who prefer them.

## Project status

Niyān is under active development and has not reached a stable v1 release. Interfaces may still change between pre-release versions. The accepted product and protocol behavior lives in the [specification index](docs/specs/README.md), and architectural decisions live in the [ADR index](docs/architecture/decisions/README.md).

## Repository layout

- `apps/server/` — Django and Django Ninja control plane
- `apps/web/` — Nuxt dashboard
- `clients/python/` — unified `niyan` CLI and Python filesystem client
- `deploy/` — Docker Compose and operations guidance
- `docs/` — product specifications and architectural decisions

Component setup is documented in each component README. Production operators should begin with the [Docker Compose deployment guide](deploy/README.md).

## Contributing and support

Read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a change. General support expectations are in [SUPPORT.md](SUPPORT.md), and vulnerabilities must follow [SECURITY.md](SECURITY.md) rather than a public issue. Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).

Niyān is licensed under the [Apache License 2.0](LICENSE).
