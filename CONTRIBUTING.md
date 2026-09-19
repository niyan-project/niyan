# Contributing to Niyān

Thank you for helping improve Niyān. The project welcomes focused bug reports, documentation corrections, design discussion, tests, and code contributions.

## Before changing code

Search the issue tracker before opening a new issue. For a substantial feature or architectural change, open an issue first so its product behavior and boundaries can be agreed before implementation. Security reports must follow [SECURITY.md](SECURITY.md).

Read the relevant files in `docs/specs/` and `docs/architecture/decisions/`. Accepted specifications define intended behavior; implementation and executable tests define what exists today. A public behavior change should update both.

## Development

The repository is a modular monorepo. Setup and verification commands are maintained in:

- [apps/server/README.md](apps/server/README.md)
- [apps/web/README.md](apps/web/README.md)
- [clients/python/README.md](clients/python/README.md)
- [deploy/README.md](deploy/README.md)

Keep changes cohesive and avoid unrelated cleanup. Add the smallest test that demonstrates the behavior or would have caught the bug. Do not add generated secrets, credentials, local databases, caches, or build output.

Commit messages use [Conventional Commits](https://www.conventionalcommits.org/). Pull requests should explain the problem, the chosen behavior, verification performed, and any operational or compatibility impact. A maintainer may ask that a large pull request be split into independently reviewable changes.

## Review and acceptance

Automated checks must pass on supported Python versions, Django with PostgreSQL, Git and Git LFS integration, Nuxt, package builds, and the production image. Maintainers evaluate changes for correctness, scope, security, compatibility with standard Git and Git LFS, and alignment with accepted specifications.

Submitting a contribution means you agree to license it under the repository's [Apache License 2.0](LICENSE).
