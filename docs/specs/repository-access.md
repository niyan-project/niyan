# Niyān Repository Access

- **Status:** Accepted
- **Audience:** server, web, CLI, and Python-client maintainers
- **Last reviewed:** 2026-09-18

## Purpose

This specification defines the first read-only access to Git-backed dataset repositories. Git remains authoritative for refs, commits, trees, and blobs. The server authenticates and authorizes requests but does not implement Git object negotiation.

## Smart Git HTTP

The initial transport supports clone and fetch through Git smart HTTP only. A dataset repository is addressed by immutable UUID at `/git/<dataset-uuid>.git`. Human-facing namespace paths are resolved through the REST API before Git is invoked.

The server accepts only:

- `GET /git/<dataset-uuid>.git/info/refs?service=git-upload-pack`; and
- `POST /git/<dataset-uuid>.git/git-upload-pack`.

Receive-pack, dumb HTTP, archive endpoints, and SSH are not supported in this read-only slice. Authenticated receive-pack is defined separately by the [Git write transport and ref-update policy](git-write-transport.md).

Git authenticates with HTTPS Basic authentication. The username is informational and the access token is the password. The token must grant `read_repository`, its resource boundary must include the dataset, and the owning user must currently have reader access. Invalid credentials return an authentication challenge; unknown and inaccessible datasets are not distinguished.

After authorization, Django invokes `git http-backend` with a UUID-derived repository path and a deliberately limited CGI environment. It forwards `Git-Protocol` for protocol-v2 negotiation, streams request and response bodies, ignores backend diagnostic output in public responses, and never forwards authorization headers or access-token values to Git.

## Repository Browsing API

Authenticated REST clients may inspect a visible dataset repository through these resources:

- refs, separated into branches and tags;
- commits reachable from a requested revision;
- a tree listing at an exact resolved commit and optional directory path;
- blob metadata at an exact resolved commit and path;
- raw Git-resident blob content; and
- a browser download action that selects the correct Git or Git LFS transfer path; and
- a root README selected from conventional names.

Every revision input is resolved to an exact commit before another repository operation occurs, and responses expose that resolved commit. Repository commands receive arguments without a shell, reject control characters, use bounded timeouts, and return sanitized errors.

Listing endpoints use bounded limit-and-offset pagination. They need not calculate an expensive total count. A non-null next offset indicates that another page may exist.

Bearer clients require `read_repository`; browser sessions rely on their current dataset authorization. Dataset-bound tokens cannot browse another repository.

## Blob Behavior

Blob metadata includes path, Git object identifier, byte size, and whether the blob is a valid Git LFS pointer. When it is an LFS pointer, metadata also exposes the SHA-256 object identifier and declared size.

Raw download of an LFS pointer returns an explicit unavailable response rather than returning the pointer text as though it were the dataset file. Ordinary Git-resident blobs stream directly from Git without loading their complete contents into Django memory and use attachment disposition for browser downloads.

The browser download action resolves the requested revision to an exact commit. For an ordinary blob it returns the authorized same-origin raw endpoint at that exact commit. For an available LFS object it returns a short-lived direct object-storage action and Django does not proxy the bytes. A missing or not-yet-available LFS object returns an explicit conflict instead of a pointer or broken storage URL.

README content is returned only for an ordinary Git-resident blob and is capped at a documented small-file limit. Rendering and sanitization belong to the web application and viewer boundary.

## Rebuildability

This slice reads repositories directly. It does not store refs, commits, paths, or blob metadata in PostgreSQL. Future indexes must remain rebuildable from the bare repository and preserve the response semantics defined here.

## Initial Non-goals

- Push, ref mutation, and the initial protected-ref baseline, which are defined by the [Git write transport and ref-update policy](git-write-transport.md).
- Defining Git LFS object transfer itself, which belongs to the focused Git LFS and object-storage protocol.
- Server-side diffs or semantic file comparison.
- Full-text search.
- Archive generation.
- Path-level read authorization.
