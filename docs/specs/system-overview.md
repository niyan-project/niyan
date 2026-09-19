# Niyān System Overview

- **Status:** Accepted
- **Audience:** maintainers, contributors, and early adopters
- **Last reviewed:** 2026-09-18

## Summary

Niyān is an open-source, self-hosted data forge for individuals, research laboratories, and machine-learning teams. It gives datasets the familiar collaboration and browsing experience of a Git forge while delegating version history to Git and large-file transfer to Git LFS and S3-compatible object storage.

The first release optimizes for a coherent, dependable dataset workflow rather than a broad data platform.

## Goals

- Represent each dataset as a hierarchical tree of files with immutable, reproducible versions.
- Let users browse a dataset's files, README, commits, branches, and tags in a web application.
- Let ordinary Git repositories pin exact dataset versions, including through Git submodules.
- Provide a `niyan` CLI as the supported interface for dataset repository workflows while delegating their mechanics to Git and Git LFS internally.
- Support users, groups, namespaces, dataset access, tokens, and branch protection.
- Transfer large objects directly to and from S3-compatible storage without routing their bytes through Django.
- Publish one `niyan` PyPI package that provides the CLI and exposes datasets as a read-only `fsspec` filesystem for streaming and downloading files on workstations, clusters, and HPC systems.
- Offer a stable, documented REST API for the web application, CLI, Python client, and other integrations.
- Allow installable viewers to display special file formats in the browser while the core remains format-agnostic.
- Be straightforward to self-host with Docker Compose and usable with externally managed PostgreSQL and S3-compatible services.
- Use open, documented protocols and formats so a dataset can outlive a Niyān installation.

## Non-goals for the Initial Product

- Data pipelines, experiment tracking, metrics, or model registry features.
- A CI/CD product.
- Semantic row-, schema-, image-, or domain-specific diffs.
- Server-side querying of tabular file contents.
- Path-level read authorization.
- A bespoke replacement for Git, the Git wire protocol, or Git LFS.
- Guaranteed support for non-S3 bulk storage backends, Windows clients, or Kubernetes deployment in the first release.
- Database snapshots, external URLs, or other non-file dataset entries.
- Dataframe, query-engine, compute-scheduling, or domain-specific analysis APIs in the Python client.

## System Model

### Dataset

A dataset is a standard Git repository. Git trees encode its directory hierarchy, blobs encode ordinary files and Git LFS pointer files, commits encode immutable versions, and refs encode branches and tags. Niyān does not assign a separate dataset commit identifier.

Niyān supports standard Git and Git LFS over HTTPS as a normal user workflow. The `niyan` CLI remains the highly recommended interface because it configures authentication and large-file behavior, supplies safer defaults, and presents dataset-oriented diagnostics, but repository validity and server authorization do not depend on using it. This follows [ADR 0011](../architecture/decisions/0011-support-standard-git-workflows.md).

A project may consume multiple datasets, and multiple projects may consume the same dataset. A Git submodule pinned to a dataset commit is a first-class integration pattern, but submodules are not the definition of a dataset and direct clones remain valid.

### Control Plane

The Django application is authoritative for:

- users, groups, and GitLab-like namespaces;
- dataset registration and discoverability;
- access grants, roles, tokens, and protected-ref policy;
- Git and Git LFS request authorization;
- storage configuration and signed transfer orchestration;
- viewer registration and public REST API behavior; and
- rebuildable indexes used to make the web interface responsive.

It is not authoritative for Git history. A database index of commits, refs, paths, or file metadata must be reconstructable from the Git repository.

### Git Data Plane

Git is authoritative for directory trees, history, branching, tagging, diffs, and merges. The server exposes authenticated Git fetch and push endpoints and applies access and protected-ref policy around those operations.

Git objects are immutable, but refs are mutable pointers. The accepted [Git write transport and ref-update policy](git-write-transport.md) permits contributor fast-forward pushes, protects the default branch from deletion, prevents in-place tag updates, and rejects every force update in v1. Retention and repository garbage collection remain separate policy concerns.

### Large-object Data Plane

Files selected by committed Git attributes are represented in Git by standard LFS pointer blobs. When `niyan add` owns staging, the accepted [CLI Git LFS tracking policy](cli-lfs-tracking.md) selects LFS for a new regular file that is binary according to Git's NUL-byte heuristic or larger than 10 MiB; existing paths preserve their storage mode. A user staging through standard Git owns their `.gitattributes` choices, and the server does not impose this CLI recommendation on ordinary Git blobs. LFS object bytes are stored in S3-compatible object storage under a layout controlled by Niyān.

For large-object upload or download, an authorized Git LFS batch request returns short-lived object-specific transfer actions. The client transfers bytes directly to or from object storage. Niyān requires successful upload finalization before a newly uploaded object is considered available to repository operations.

The accepted [Git LFS and object-storage protocol](git-lfs-and-object-storage.md) defines batch negotiation, direct and multipart transfers, upload finalization, a seven-day failed-push grace period, and automatic orphan cleanup. One private installation bucket uses dataset UUID-scoped keys as defined by [ADR 0007](../architecture/decisions/0007-shared-s3-bucket.md). Physical keys and provider checksum capabilities remain internal deployment details and must not leak into the public dataset model.

### Web Application

The Nuxt application provides a Git-forge-style dataset view with namespace navigation, a default README when present, tree browsing, breadcrumbs, commits, branches, tags, and file download. Format-specific previews come from installable viewer plugins rather than hard-coded core behavior.

The web application is a client-rendered Nuxt 4 SPA distributed as static files under the same origin as Django. It uses Nuxt UI and Tailwind CSS, an Indigo primary color with neutral surfaces, and light, dark, and system-following appearances. The product has no public repositories, stars, followers, or other social-forge features in v1. The accepted [web application specification](web-application.md) defines its deployment, security, design, and Phase 3 workflow boundary.

Manual browser upload should be possible, even though the CLI is the primary workflow. The exact staging and commit interaction is tracked by [Issue #36](https://github.com/niyan-project/niyan/issues/36).

### CLI

The `niyan` CLI is the recommended dataset-oriented workflow and coordinates Git, Git LFS, and REST operations internally. It provides exact-version workflows, selective materialization, shallow-history defaults, automatic LFS attribute management, multipart upload configuration, and Niyān-specific diagnostics.

Direct Git and Git LFS operations against a Niyān dataset are also supported. Standard clients use the same access tokens, smart-HTTP repository endpoints, Git LFS Batch API, and server policy. Users who choose those clients are responsible for ordinary Git decisions such as clone depth, merge strategy, and `.gitattributes` rules, and may not receive every convenience or resilient-transfer feature of the Niyān CLI.

The CLI is the console entry point of the unified `niyan` Python distribution. `pipx install niyan` is the standard isolated PyPI installation and does not ask users to create or activate a virtual environment themselves. A separate bootstrap installer is not part of v1.

### Python Filesystem Client

The same `niyan` distribution exposes a proper Python API whose v1 library surface is a read-only, `fsspec`-compatible filesystem. It allows research code and established Python libraries to list dataset paths, open remote files, stream byte ranges, and fully download files without cloning the dataset, installing Git or Git LFS, or invoking the CLI.

The CLI and filesystem client are separate internal modules with a shared foundation for HTTP, authentication, configuration, models, errors, and transfers. Filesystem use must not initialize or invoke CLI behavior, and importing the package must not trigger Git inspection, credential access, or network activity.

The client resolves a dataset path and requested revision through the public REST API. Before reading data, it resolves branches or tags to an exact Git commit and exposes that resolved commit to callers. A recursive download must use one resolved commit for its entire operation so a moving branch cannot produce a mixed-version directory.

For an LFS-backed file, the API returns metadata and a short-lived authorized transfer URL. The client reads the object directly from S3-compatible storage, using HTTP range requests for seeking and partial reads when available. It must refresh expired transfer authorization without exposing raw storage credentials. Small Git-resident files must present the same filesystem interface even if their internal transfer path differs.

The client never buffers a complete large file merely to satisfy `open()` or download semantics. Full downloads are streamed incrementally to local storage; interrupted or partial results must not be mistaken for successful files. The detailed method set, URI grammar, cache behavior, retry policy, and authentication configuration are defined in the [Python filesystem client specification](python-client.md).

## Identity and Authorization

An installation initially represents one organization while allowing nested groups and user namespaces. Users may own datasets in their personal namespace, subject to installation policy.

The confidentiality boundary is the dataset. A principal who can read a dataset can read its reachable Git and LFS objects and history. Branch rules may restrict creation, update, force-push, or deletion of refs, but must not be advertised as secure read isolation between branches because Git object negotiation does not provide that boundary.

The initial ordered roles are reader, contributor, maintainer, and owner. The [authorization specification](authorization.md) defines their dataset capabilities, while the [Git write transport policy](git-write-transport.md) defines the baseline ref operations available to contributors and maintainers. Browser login and scoped access tokens are required authentication modes; service identities and institutional single sign-on remain to be specified.

## Core Workflows

### Publish a Dataset

1. A user creates a dataset in a namespace through the API, web application, or CLI.
2. Niyān creates or registers its Git repository and LFS endpoint.
3. The `niyan` CLI adds files and creates dataset commits while delegating repository and large-file mechanics to Git and Git LFS.
4. Under CLI orchestration, Git LFS uploads missing objects directly to S3-compatible storage using authorized transfer actions.
5. The CLI invokes Git to push the commit and proposed ref update.
6. The server authenticates the actor and applies the [Git write transport and ref-update policy](git-write-transport.md), including required LFS-object availability, before accepting the update.
7. Rebuildable indexes and the web view catch up to the accepted Git state.

The [Git LFS protocol](git-lfs-and-object-storage.md) and [Git write transport policy](git-write-transport.md) define the transaction boundary: uploads become available before receive-pack, pre-receive validates and leases required objects before ref visibility, and recoverable post-receive work marks references and schedules indexing.

### Consume a Dataset from a Project

1. The `niyan` CLI adds the dataset to the project, commonly as a Git submodule pinned to an exact commit.
2. The user authenticates through the `niyan` CLI using a browser flow or access token.
3. The CLI invokes Git and Git LFS internally to obtain repository objects and only the required large-file content.
4. The project remains reproducible because its Git history pins an immutable dataset commit instead of following a moving branch.

### Browse a Dataset

1. The server authorizes access at the dataset boundary.
2. The web application requests tree, history, and metadata through the REST API.
3. A file download or viewer session receives a short-lived, narrowly scoped transfer URL after authorization.
4. The browser obtains Git LFS content directly from object storage. A small Git-resident blob may stream through the authorized repository endpoint; Django never proxies bulk LFS bytes.

### Stream or Download Data from Python

1. A user installs `niyan` from PyPI on a workstation, cluster login node, or HPC compute environment.
2. The client authenticates to the Niyān REST API using a token or other supported headless credential.
3. The requested dataset revision is resolved to an exact Git commit, and the client lists or resolves paths at that commit.
4. Opening a large file obtains short-lived transfer authorization and returns an `fsspec`-compatible binary file object.
5. Reads and seeks request only the needed byte ranges; a full download streams the object incrementally to local storage.
6. Expired transfer URLs are refreshed through Niyān without disclosing object-store credentials to the user.

## Deployment Boundary

Docker Compose remains the intended easy self-hosting path, but it is not a prerequisite for initial development. The server must accept externally managed PostgreSQL and S3-compatible services through environment configuration. Initial development may use remote infrastructure and does not require local PostgreSQL or S3 containers.

The generated Nuxt SPA is served under the Django origin. Build assets use `/static/niyan/`, while Django-owned API, Git, admin, and static routes remain distinct and all other browser paths fall back to the SPA entry document. A Node.js runtime is required to build and develop the frontend but not to run the initial production deployment.

A bundled maintenance worker performs failed-upload and incomplete-multipart cleanup defined by the [Git LFS and object-storage protocol](git-lfs-and-object-storage.md). It uses PostgreSQL-backed state and does not require Redis or Celery in v1. Docker Compose and production deployment guidance run the worker by default. Other asynchronous infrastructure may be introduced only after a concrete workload requires it.

Administrators are responsible for coordinated backups of Git repositories, PostgreSQL, and object storage. Niyān should document consistency requirements and eventually support portable dataset export, but it is not a general backup system.

## Tracked V1 Decisions

The accepted overview does not imply that every component contract is finished. The configurable protected-ref and browser-commit decisions are now defined by their focused accepted specifications. These remaining genuinely unresolved v1 decisions have explicit tracker ownership:

- [Issue #41](https://github.com/niyan-project/niyan/issues/41) defines the viewer plugin contract, trust model, and isolation boundary.
- [Issue #47](https://github.com/niyan-project/niyan/issues/47) defines coordinated backup, restore, upgrade, and disaster-recovery behavior across PostgreSQL, Git repositories, and object storage.
- [Issue #50](https://github.com/niyan-project/niyan/issues/50) selects the open-source license and project-governance model.
- [Issue #52](https://github.com/niyan-project/niyan/issues/52) publishes the final v1 API, protocol, on-disk, portability, and recovery compatibility contract.

Full LFS reachability garbage collection after ref deletion or future history rewriting is an explicit v1 non-goal rather than an unresolved promise. Repository browsing reads Git directly for correctness in v1; any later metadata index is a rebuildable optimization and may be eventually consistent.
