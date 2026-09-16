# ADR 0002: Model Datasets as Git Repositories

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

Niyān needs immutable file trees, content identifiers, commits, branches, tags, diffs, merges, concurrent-update protection, transport, and a way for research projects to pin an exact dataset version. Implementing a parallel version graph would duplicate mature Git behavior and create additional correctness and interoperability risk.

## Decision

Represent every dataset as a standard Git repository. Git is authoritative for tree and history semantics, and Git object identifiers are the dataset version identifiers.

Niyān will provide hosting, authorization, browsing, policy, and workflow assistance around Git rather than reimplementing Git's bookkeeping.

A project may pin a dataset using a Git submodule. This is a supported consumption pattern, not a requirement that every dataset appear only as a submodule.

## Consequences

- Niyān inherits well-understood commit, branch, tag, merge, and transport behavior.
- Standard Git tooling remains useful, and datasets are not locked to a proprietary history format. Yet, they should sit behind our own tooling and clients.
- The control plane still must supply identity, ACLs, branch protection, audit behavior, quotas, and repository lifecycle management; Git does not provide those multi-user forge concerns.
- Branches are mutable refs, so stronger immutability and retention claims require explicit server policy.
- Git repository scale and checkout behavior constrain the first version. Niyān accepts Git and Git LFS limitations initially rather than adding an external manifest.
