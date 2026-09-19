# Project governance

Niyān is maintainer-led. Maintainers set product direction, accept specifications and architectural decisions, review contributions, manage repository access, and decide what ships. Significant durable choices should be discussed in an issue and recorded in the appropriate specification or architecture decision rather than existing only in a review thread.

The repository owner is the release owner for v1 and controls GitHub release creation, PyPI trusted-publisher configuration, and production image publication. Release credentials are not shared with untrusted workflow contexts. Additional maintainers and release owners may be added as the contributor community grows.

Consensus is preferred, but it is not required when a decision must be made. Maintainers may decline changes that are correct in isolation but conflict with project scope, security boundaries, maintainability, standard Git and Git LFS compatibility, or accepted product behavior. Decisions can be revisited when new evidence or requirements emerge.

Project governance covers only the community repository and its releases. It does not create support guarantees or override the [Apache License 2.0](LICENSE).
