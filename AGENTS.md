# Niyān Repository Instructions

## Scope

This file applies to the entire repository. Add a narrower `AGENTS.md` only when a component has real commands or conventions that differ from these root instructions. A nearer file may refine these rules for its subtree but must not silently contradict the project-wide architectural invariants below.

## Product

Niyān is an open-source, self-hosted data forge for researchers and machine-learning teams. A dataset is a standard Git repository whose large file content is stored through Git LFS in S3-compatible object storage. The Django control plane supplies identity, authorization, namespaces, policy, and a public REST API; the Nuxt application supplies the web experience; the standalone `niyan` CLI makes the Git and Git LFS workflow approachable; and a separate Python package provides read-only `fsspec` access to dataset files.

Niyān builds around Git. It must not create a second, competing model of commits, trees, branches, tags, or merges.

## Sources of Truth

- Accepted behavior and protocol requirements belong in `docs/specs/`.
- Durable architectural choices and their tradeoffs belong in `docs/architecture/decisions/`.
- Code and executable tests describe what is implemented now.
- Component READMEs and checked-in tool configuration define actual development commands and style.
- This file is an operating guide, not a substitute for the specifications.

Documents marked `Draft` or ADRs marked `Proposed` are review material, not settled behavior. If implementation, tests, and accepted documentation disagree, surface the conflict and update them together instead of silently choosing one.

## Architectural Invariants

- A dataset is an ordinary Git repository.
- Git owns the dataset tree, commit graph, refs, tags, merges, and object identifiers.
- Large files use standard Git LFS pointer objects. Do not introduce a parallel Niyān manifest or DVC compatibility layer.
- S3-compatible object storage is the only v1 bulk-data backend.
- Bulk uploads and downloads go directly between the client and object storage using short-lived authorized transfer URLs. Django must not proxy bulk file bytes.
- Django owns users, groups, namespaces, access policy, branch rules, tokens, and API orchestration.
- PostgreSQL is authoritative for control-plane state. Any indexed Git metadata in PostgreSQL must be rebuildable from the repository.
- Read authorization is at the dataset boundary. There are no path-level read ACLs. Branch protection governs ref mutation; it must not be presented as confidential branch-level read isolation.
- The core remains format-agnostic. Format-specific browser experiences belong in viewer plugins.
- The Python client is a proper PyPI package, distinct from the CLI. Its v1 purpose is read-only, `fsspec`-compatible streaming and downloading without requiring a Git checkout or direct S3 credentials.
- The REST API is a public product surface and must be versioned deliberately.
- The product does not include experiment tracking, metrics, data pipelines, or a CI/CD system.
- Protocols and on-disk formats must be documented so users are not trapped in a Niyān server.

## Planned Repository Shape

The repository is a modular monorepo. Directories may be introduced incrementally:

```text
apps/server/                    Django and Django Ninja control plane
apps/web/                       Nuxt and Vue web application
clients/cli/                    Standalone niyan CLI
clients/python/                 PyPI package and fsspec filesystem
packages/viewer-sdk/            Viewer plugin contracts and helpers
plugins/examples/               Example viewer plugins
contracts/                      Shared and generated API contracts
tests/e2e/                      Cross-component workflow tests
deploy/                         Docker Compose and deployment assets
docs/specs/                     Product and protocol specifications
docs/architecture/decisions/    Architecture decision records
```

Keep component boundaries real:

- The CLI communicates through documented Git, Git LFS, and REST interfaces; it must not import server internals.
- The Python client communicates through the public REST and authorized data-transfer interfaces. It must not shell out to the CLI, require a local Git checkout, or import server internals.
- The web application treats the REST API as its server boundary. Authorization decisions are never delegated to browser code.
- Viewer plugins consume an explicit, versioned contract. They must not reach into server or web internals.
- Shared code must have a genuine cross-component contract. Do not create a generic dumping-ground package.

## Working Agreements

Before changing behavior, read the relevant spec and ADRs. While working:

- Preserve unrelated user changes and keep each change focused.
- Prefer standard Git and Git LFS behavior over custom protocol or storage logic.
- Do not add speculative compatibility layers or abstractions for unapproved future backends.
- Update the relevant specification in the same change when public behavior changes.
- Add or amend an ADR when a change alters a project-wide architectural decision.
- Document new production dependencies and explain why an existing dependency or standard library facility is insufficient.
- Keep public error messages actionable and avoid exposing implementation details or secrets.
- Do not commit, push, tag, publish, or release unless the user explicitly requests it.

Do not invent formatter, package-manager, migration, or test commands before the corresponding tooling exists. Once checked-in configuration exists, use it rather than documenting a second set of rules here.

## Change Process

- Before implementing a change, inspect the relevant code and documentation and present a staged plan.
- Read-only investigation and planning do not require approval.
- Do not begin implementing a stage until the user explicitly approves that stage.
- Keep each stage small, cohesive, and independently reviewable. If a stage becomes difficult to review as one change, stop and split it before proceeding.
- After implementing an approved stage, summarize the files changed, decisions made, and verification performed, then stop for review.
- Do not begin the next stage, commit the completed stage, or combine it with unrelated work until the user explicitly approves.
- Avoid opportunistic refactors, cleanup, renaming, or formatting outside the approved stage. You can, however, suggest them to be done after the task at hand.
- Each commit should contain one reviewed, cohesive change. Never create a commit merely because implementation is complete.
- Warn the user if they are about to break these conventions when manually committing.
- Use "Conventional Commits" for commit messages.

## Formatting and Style

- Do not wrap code, signatures, expressions, imports, or documentation solely to satisfy a conventional print-width or column limit.
- Break lines when it improves structure or comprehension, not because a fixed width has been exceeded.
- Do not enable line-length lint failures or introduce a formatter that forces aggressive wrapping without explicit approval.
- Preserve the existing formatting of surrounding code unless changing it is part of the approved stage.
- NumPy-style docstrings are a must for classes and their methods. Line comments should also be included to explain decisions and designs that are not otherwise inferrable from the code itself.

## Component Guidance

### Server

- Use Django for the application and Django Ninja for the versioned REST API.
- Keep HTTP handlers thin; put policy and domain behavior in testable services.
- Enforce authorization server-side before repository access or transfer URLs are granted.
- Treat Git operations, object-store responses, webhook data, and client manifests as untrusted input.
- Use migrations for persistent schema changes.
- Keep network and storage integrations behind small interfaces that can be exercised with local test doubles.

### Web

- Use TypeScript and Vue Composition API conventions supported by the checked-in Nuxt configuration.
- Keep authorization and integrity decisions on the server; the UI may explain policy but may not enforce it alone.
- Build repository browsing around Git concepts already present in the domain rather than inventing parallel dataset-version terminology.
- Treat rendered README files and viewer output as untrusted content.

### CLI

- The CLI is a Git extension and may require Git; it is not a standalone replacement for Git.
- Distribute the CLI independently from the Python client. The primary installation path should be a single-command bootstrap installer, with checksums and a documented manual alternative.
- Delegate repository mechanics to Git and transfers to Git LFS wherever their documented behavior is sufficient.
- Keep stdout suitable for requested command output and use stderr for diagnostics and progress.
- Support non-interactive use, streaming transfers, resumable behavior where the underlying protocol allows it, and clear exit codes.
- Never persist raw credentials in repository-tracked files.

### Python Client

- Publish the Python client as an independently versioned PyPI package with an `fsspec`-compatible, read-only filesystem.
- Keep the v1 scope narrow: list and inspect paths, open files for binary streaming, perform ranged reads and seeks where supported, and download files without loading them entirely into memory.
- Resolve a dataset revision to an exact Git commit before transferring content. Recursive operations must remain pinned to that commit for their duration.
- Obtain authorized transfer URLs through the public API and refresh expired URLs transparently. Never require or expose raw S3 credentials.
- Do not require Git, Git LFS, the `niyan` CLI, or a repository checkout at runtime.
- Preserve standard `fsspec` behavior so pandas, Polars, PyArrow, xarray, Dask, and similar consumers can use the filesystem without Niyān-specific adapters.
- Treat HPC and headless systems as first-class environments: support token authentication, low-memory streaming, clear timeouts, and interruption-safe downloads.

## Security and Data Integrity

- Authorize every repository and LFS operation before disclosing metadata or issuing a transfer URL.
- Scope signed transfer URLs to the required object and operation, and keep them short-lived.
- Never log credentials, access tokens, session secrets, or signed URLs.
- Do not treat an S3 ETag as a portable content checksum.
- Verify the expected Git LFS object identifier and size before making a ref update visible when the protocol flow requires newly uploaded objects.
- Ref updates must use Git's expected-old-object semantics so concurrent writers cannot silently overwrite one another.
- Stream large content; never assume a dataset object fits in application memory or local disk.
- Viewer execution and rendering must be isolated according to the eventual viewer specification.

## Testing

- Add the smallest test that would have caught a bug or proves new behavior.
- Unit-test domain policy without requiring Git, S3, or a browser when practical.
- Integration-test boundaries with real Git repositories and an S3-compatible test service where semantics matter.
- Protect the canonical end-to-end flow: create a dataset, authenticate, push Git and LFS objects, browse history, pin it from another repository, pull the data, and stream a pinned file through the Python client.
- Test denied access and interrupted/concurrent transfers, not only successful paths.
- Keep tests deterministic and independent of public network services.

## Documentation

- Begin with the [specification index](docs/specs/README.md).
- Record architectural decisions in the [ADR index](docs/architecture/decisions/README.md).
- Use repository-relative links inside documentation.
- Prefer examples that use fictitious data and credentials.
- Mark unresolved design choices explicitly instead of presenting guesses as settled requirements.
