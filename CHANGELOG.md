# Changelog

All notable user-facing changes to Niyān will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and released versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While Niyān remains below `1.0.0`, minor releases may change public interfaces and patch releases remain backwards-compatible bug fixes.

## [Unreleased]

## [0.4.0] - 2026-09-22

### Added

- Delegated system administration for Niyān users, datasets, groups, staff accounts, and Django permission groups.
- Native Git and Git LFS progress during interactive `niyan push`, with an explicit quiet mode.

### Changed

- Made `niyan add` delegate to Git by default so repository `.gitattributes` rules remain authoritative without per-file classification overhead.
- Enabled concurrent independent-object Git LFS transfers using the standard `lfs.concurrenttransfers` setting and an eight-worker default.
- Batched accepted-ref and Git LFS post-receive bookkeeping so many-file pushes use a bounded number of PostgreSQL queries.

### Fixed

- Accepted chunked Git smart-HTTP push bodies through the production Caddy and Gunicorn deployment path.

## [0.3.0] - 2026-09-22

### Added

- Account profile editing for users' first and last names.
- Linked breadcrumbs across nested groups and dataset repository pages.

### Changed

- Rendered dataset descriptions as sanitized Markdown.
- Made generated staff-user passwords immediately visible and copyable before account creation.
- Simplified the account settings navigation and renamed "Password and authentication" to "Password".
- Targeted continuous-integration jobs by affected component and automated documentation deployment to GitHub Pages.

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

[Unreleased]: https://github.com/niyan-project/niyan/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/niyan-project/niyan/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/niyan-project/niyan/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/niyan-project/niyan/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/niyan-project/niyan/releases/tag/v0.1.0
