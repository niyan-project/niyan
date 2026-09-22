# Changelog

All notable user-facing changes to Niyān will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and released versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While Niyān remains below `1.0.0`, minor releases may change public interfaces and patch releases remain backwards-compatible bug fixes.

## [Unreleased]

## [0.2.0] - 2026-09-22

### Added

- Staff administration for users, groups, and datasets, including secure initial-password generation.
- Cross-surface authorization, concurrent Git update, interrupted-transfer, recovery-rehearsal, and large-repository hardening suites.
- A self-contained single-node Compose deployment with PostgreSQL, SeaweedFS object storage, automatic HTTPS, and the maintenance process.
- A version-pinned deployment bootstrap that downloads only the Compose bundle and operator runbook.
- Versioned, multi-platform GHCR images with release provenance attestations.
- A public VitePress website with separate user, CLI, dashboard, and operator documentation.

### Changed

- Reused one S3 adapter per Git LFS Batch request, reducing measured 100-object negotiation latency from roughly 44.5 seconds to 1.77 seconds.
- Raised and enforced component coverage gates while moving all server testing to PostgreSQL.
- Moved maintainer specifications and architecture decisions to the project Wiki.

## [0.1.0] - 2026-09-19

### Added

- Self-hosted Django and Nuxt control plane for ordinary Git and Git LFS dataset repositories.
- Unified `niyan` CLI and read-only `fsspec` filesystem client.
- Direct, authorized S3-compatible transfers including resilient multipart uploads.
- Docker Compose deployment, operational health checks, and cross-component continuous integration.

[Unreleased]: https://github.com/niyan-project/niyan/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/niyan-project/niyan/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/niyan-project/niyan/releases/tag/v0.1.0
