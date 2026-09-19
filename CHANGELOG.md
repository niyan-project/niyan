# Changelog

All notable user-facing changes to Niyān will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and released versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While Niyān remains below `1.0.0`, minor releases may change public interfaces and patch releases remain backwards-compatible bug fixes.

## [Unreleased]

### Added

- Self-hosted Django and Nuxt control plane for ordinary Git and Git LFS dataset repositories.
- Unified `niyan` CLI and read-only `fsspec` filesystem client.
- Direct, authorized S3-compatible transfers including resilient multipart uploads.
- Docker Compose deployment, operational health checks, and cross-component continuous integration.
