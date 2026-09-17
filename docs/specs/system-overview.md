# Niyān System Overview

- **Status:** Draft
- **Audience:** maintainers, contributors, and early adopters
- **Last reviewed:** not yet reviewed

## Summary

Niyān is an open-source, self-hosted data forge for individuals, research laboratories, and machine-learning teams. It gives datasets the familiar collaboration and browsing experience of a Git forge while delegating version history to Git and large-file transfer to Git LFS and S3-compatible object storage.

The first release optimizes for a coherent, dependable dataset workflow rather than a broad data platform.

## Goals

- Represent each dataset as a hierarchical tree of files with immutable, reproducible versions.
- Let users browse a dataset's files, README, commits, branches, and tags in a web application.
- Let ordinary Git repositories pin exact dataset versions, including through Git submodules.
- Provide a standalone `niyan` CLI as the supported interface for dataset repository workflows while delegating their mechanics to Git and Git LFS internally.
- Support users, groups, namespaces, dataset access, tokens, and branch protection.
- Transfer large objects directly to and from S3-compatible storage without routing their bytes through Django.
- Publish a separate PyPI package that exposes datasets as a read-only `fsspec` filesystem for streaming and downloading files on workstations, clusters, and HPC systems.
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

Niyān preserves standard Git repository and object formats, but users are expected to create and mutate dataset history through the `niyan` CLI rather than invoking the Git or Git LFS CLIs directly. Git compatibility keeps the data portable and gives administrators a recovery and maintenance escape hatch; it is not a parallel supported product workflow.

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

Git objects are immutable, but refs are mutable pointers. Force-push, deletion, retention, and repository garbage-collection policy require explicit specification before the product can claim stronger history immutability than Git itself provides.

### Large-object Data Plane

Files selected for Git LFS are represented in Git by standard LFS pointer blobs. Their bytes are stored in S3-compatible object storage under a layout controlled by Niyān.

For large-object upload or download, an authorized Git LFS batch request returns short-lived object-specific transfer actions. The client transfers bytes directly to or from object storage. Niyān may require a verification call before an uploaded object is considered available to repository operations.

The physical bucket topology, orphan grace period, garbage-collection algorithm, and retention rules are intentionally unresolved. They must not leak into the public dataset model.

### Web Application

The Nuxt application provides a Git-forge-style dataset view with namespace navigation, a default README when present, tree browsing, breadcrumbs, commits, branches, tags, and file download. Format-specific previews come from installable viewer plugins rather than hard-coded core behavior.

Manual browser upload should be possible, even though the CLI is the primary workflow. The exact staging and commit interaction remains to be specified.

### CLI

The standalone `niyan` CLI owns the supported dataset repository workflow and coordinates Git, Git LFS, and REST operations internally. Git and Git LFS may be required implementation dependencies for the initial CLI, but users should not need to understand or invoke them directly. The CLI must support exact-version workflows and selective file or directory retrieval where standard Git LFS behavior permits it.

Direct Git operations against a Niyān dataset may remain technically possible because the repository and transport are standard, but they are outside the supported user experience. Niyān documentation, diagnostics, authentication setup, and compatibility guarantees target CLI-mediated operations.

The CLI is distributed independently from the Python package. Its primary installation experience should be a single-command bootstrap installer, with published checksums and a documented manual installation path. The implementation language and packaging format are not yet selected.

### Python Filesystem Client

Niyān publishes a proper Python package on PyPI whose v1 product surface is a read-only, `fsspec`-compatible filesystem. It allows research code and established Python libraries to list dataset paths, open remote files, stream byte ranges, and fully download files without cloning the dataset or installing Git, Git LFS, or the Niyān CLI.

The client resolves a dataset path and requested revision through the public REST API. Before reading data, it resolves branches or tags to an exact Git commit and exposes that resolved commit to callers. A recursive download must use one resolved commit for its entire operation so a moving branch cannot produce a mixed-version directory.

For an LFS-backed file, the API returns metadata and a short-lived authorized transfer URL. The client reads the object directly from S3-compatible storage, using HTTP range requests for seeking and partial reads when available. It must refresh expired transfer authorization without exposing raw storage credentials. Small Git-resident files must present the same filesystem interface even if their internal transfer path differs.

The client never buffers a complete large file merely to satisfy `open()` or download semantics. Full downloads are streamed incrementally to local storage; interrupted or partial results must not be mistaken for successful files. The detailed method set, URI grammar, cache behavior, retry policy, and authentication configuration are defined in the [Python filesystem client specification](python-client.md).

## Identity and Authorization

An installation initially represents one organization while allowing nested groups and user namespaces. Users may own datasets in their personal namespace, subject to installation policy.

The confidentiality boundary is the dataset. A principal who can read a dataset can read its reachable Git and LFS objects and history. Branch rules may restrict creation, update, force-push, or deletion of refs, but must not be advertised as secure read isolation between branches because Git object negotiation does not provide that boundary.

Initial role names and exact permissions are not yet accepted. The model should distinguish at least reading, contributing, maintaining protected refs and settings, and owning a namespace or dataset. Browser login and scoped access tokens are required authentication modes; service identities and institutional single sign-on remain to be specified.

## Core Workflows

### Publish a Dataset

1. A user creates a dataset in a namespace through the API, web application, or CLI.
2. Niyān creates or registers its Git repository and LFS endpoint.
3. The `niyan` CLI adds files and creates dataset commits while delegating repository and large-file mechanics to Git and Git LFS.
4. Under CLI orchestration, Git LFS uploads missing objects directly to S3-compatible storage using authorized transfer actions.
5. The CLI invokes Git to push the commit and proposed ref update.
6. The server authenticates the actor, enforces dataset and protected-ref policy, and ensures required LFS objects are available before accepting the update.
7. Rebuildable indexes and the web view catch up to the accepted Git state.

The exact transaction boundary between LFS verification, Git receive, and index updates needs a dedicated protocol specification.

### Consume a Dataset from a Project

1. The `niyan` CLI adds the dataset to the project, commonly as a Git submodule pinned to an exact commit.
2. The user authenticates through the `niyan` CLI using a browser flow or access token.
3. The CLI invokes Git and Git LFS internally to obtain repository objects and only the required large-file content.
4. The project remains reproducible because its Git history pins an immutable dataset commit instead of following a moving branch.

### Browse a Dataset

1. The server authorizes access at the dataset boundary.
2. The web application requests tree, history, and metadata through the REST API.
3. A file download or viewer session receives a short-lived, narrowly scoped transfer URL after authorization.
4. The browser obtains the file directly from object storage or an explicitly defined viewer service, not through the Django application process.

### Stream or Download Data from Python

1. A user installs the Python client from PyPI on a workstation, cluster login node, or HPC compute environment.
2. The client authenticates to the Niyān REST API using a token or other supported headless credential.
3. The requested dataset revision is resolved to an exact Git commit, and the client lists or resolves paths at that commit.
4. Opening a large file obtains short-lived transfer authorization and returns an `fsspec`-compatible binary file object.
5. Reads and seeks request only the needed byte ranges; a full download streams the object incrementally to local storage.
6. Expired transfer URLs are refreshed through Niyān without disclosing object-store credentials to the user.

## Deployment Boundary

Docker Compose remains the intended easy self-hosting path, but it is not a prerequisite for initial development. The server must accept externally managed PostgreSQL and S3-compatible services through environment configuration. Initial development may use remote infrastructure and does not require local PostgreSQL or S3 containers.

A background worker and Redis may be introduced for indexing, cleanup, or other asynchronous jobs only after those workloads are specified. They are not architectural requirements merely because they are common Django infrastructure. Docker Compose should not include them until a concrete workload requires them.

Administrators are responsible for coordinated backups of Git repositories, PostgreSQL, and object storage. Niyān should document consistency requirements and eventually support portable dataset export, but it is not a general backup system.

## Open Questions

- What is the exact Git LFS upload verification and failed-push cleanup protocol?
- How are Git repositories stored and backed up independently of LFS objects?
- How should an externally hosted PostgreSQL service be deployed, secured, backed up, and upgraded for development and self-hosted installations?
- What retention, legal purge, garbage-collection, and history-rewrite policies are supported?
- Which roles and token scopes form the smallest coherent authorization model?
- What is the viewer plugin API, trust model, and isolation boundary?
- What are the CLI command surface, local cache behavior, partial-fetch behavior, implementation language, and machine-readable output contract?
- What package name, filesystem URI grammar, supported Python versions, authentication configuration, cache defaults, and async guarantees should the Python client expose?
- Which operations are indexed synchronously, and which may be eventually consistent in the web application?
- What portable export format preserves complete Git and LFS history across installations?
- Which open-source license best protects the desired community model?
