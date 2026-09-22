# Niyān

[![Continuous integration](https://github.com/niyan-project/niyan/actions/workflows/ci.yml/badge.svg)](https://github.com/niyan-project/niyan/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/github/niyan-project/niyan/graph/badge.svg)](https://app.codecov.io/github/niyan-project/niyan)
[![License](https://img.shields.io/github/license/niyan-project/niyan)](LICENSE)

Niyān is an open-source, self-hosted data forge for researchers and machine-learning teams. Each dataset is an ordinary Git repository; Git owns commits, trees, branches, tags, and merges, while standard Git LFS stores large content in private S3-compatible object storage. Django provides identity, authorization, policy, and a public REST API, and Nuxt provides the web application.

The `niyan` Python package combines the recommended command-line experience with a read-only `fsspec` filesystem for checkout-free streaming on workstations and HPC systems. Standard Git and Git LFS over HTTPS remain supported for users who prefer them.

## Project status

Niyān is under active development and has not reached a stable v1 release. Interfaces may still change between pre-release versions. The accepted product and protocol behavior lives in the [Wiki specification index](https://github.com/niyan-project/niyan/wiki/Specifications), architectural decisions live in the [Wiki architecture decision index](https://github.com/niyan-project/niyan/wiki/Architecture-Decisions), and user-facing release changes are recorded in the [changelog](CHANGELOG.md).

## Repository layout

- `apps/server/` — Django and Django Ninja control plane
- `apps/web/` — Nuxt dashboard
- `clients/python/` — unified `niyan` CLI and Python filesystem client
- `deploy/` — Docker Compose and operations guidance
- `docs/` — public landing page and user documentation

Component setup is documented in each component README. Production operators should begin with the [Docker Compose deployment guide](deploy/README.md).

Versioned server images are published at `ghcr.io/niyan-project/niyan`. The default Compose deployment combines that image with PostgreSQL, SeaweedFS object storage, Caddy automatic HTTPS, and durable volumes; operators with existing infrastructure can run the same image directly.

## Contributing and support

Read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a change. General support expectations are in [SUPPORT.md](SUPPORT.md), and vulnerabilities must follow [SECURITY.md](SECURITY.md) rather than a public issue. Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).

Niyān is licensed under the [Apache License 2.0](LICENSE).

Maintainers publish the unified Python package and server image using the documented [release process](RELEASING.md).
