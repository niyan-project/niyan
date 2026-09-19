# Browser Uploads and Commits

- **Status:** Accepted
- **Audience:** server, web, storage, and authorization maintainers
- **Last reviewed:** 2026-09-18

## Purpose

The web application must let an authorized contributor add, replace, and delete dataset files and create an ordinary Git commit without installing the CLI. The workflow remains explicit: uploading or staging a file does not publish a dataset version, and Django never proxies large file bodies.

Git remains authoritative for the resulting tree, commit, parent, author, and branch update. Browser drafts are temporary orchestration state rather than a second version model.

## Drafts

A draft belongs to one user and dataset and records a target branch, the exact base commit observed when the draft starts, expiry, and staged path operations. Only its creator may view or mutate it. A dataset may have several independent drafts, but one draft targets one branch and base commit.

The interface supports adding or replacing regular files and deleting existing files. Directories arise from file paths and disappear when empty. Explicit rename is not a separate v1 operation; deleting one path and adding another produces the same Git tree. Empty directories, symlinks, submodules, device entries, and executable-bit editing are not supported through browser drafts.

Repository paths are normalized POSIX paths. Absolute paths, empty components, `.` or `..`, control characters, `.git` administration paths, and paths that collide as both a file and directory are rejected. A later operation on the same path replaces the earlier staged operation.

## Upload Classification and Transfer

The web client applies the same default classification as `niyan add`: a regular file uses Git LFS when it is larger than 10 MiB or contains a NUL byte in its first 8,000 bytes. The user may explicitly select Git or Git LFS storage before upload. Classification is a convenience and compatibility decision, not an authorization boundary.

Git LFS content uses the existing dataset-scoped direct-transfer protocol. The browser obtains short-lived signed actions after authorization and uploads bytes directly to S3-compatible storage. Draft state records only the expected Git LFS SHA-256 identifier, size, and verified server-side object record; it never stores a signed URL or storage credential.

The S3-compatible deployment must allow the configured Niyān web origin to perform signed `PUT` requests and must expose `ETag` plus any provider checksum response headers through CORS. This is required for browser multipart completion; it does not make objects public.

Ordinary Git content is limited to 10 MiB per staged file and may be sent through a bounded authenticated Django endpoint. Django streams it into the bare repository's object database, verifies the declared size, and records the resulting Git blob identifier without retaining a second copy in PostgreSQL. A client cannot force content above that bound into the Git upload endpoint.

## Commit Publication

The user reviews the staged tree and supplies a non-empty commit message. Publication then:

1. reauthorizes the user and token or browser session for repository writes;
2. verifies the target branch still points to the draft's exact base commit, or remains unborn for an initial `main` commit;
3. rechecks protected-ref policy for the target branch;
4. verifies every referenced Git LFS object is available for the dataset;
5. constructs the tree and ordinary Git commit directly through Git plumbing with the authenticated user's identity;
6. updates the branch using expected-old-object semantics; and
7. marks the draft committed and records an audit event.

The commit has one parent: the exact base commit. Browser merge commits, rebases, amend, signing, author overrides, and arbitrary parent selection are outside v1. The server timestamp is the commit author and committer time.

If the branch moved after draft creation, publication returns a conflict and leaves the draft available for inspection. It never silently merges, rebases, or commits against the newer tree. If validation, object verification, commit construction, or ref update fails, the target ref remains unchanged and the draft is not reported as committed. Unreachable Git objects may remain for ordinary Git maintenance; unreferenced LFS objects follow the existing grace-period cleanup policy.

## Web Experience

Authorized users start an upload from a dataset branch view. The draft page shows the base commit, target branch, staged additions, replacements, and deletions, storage choice, upload progress, validation failures, and the final commit message. Leaving the page does not implicitly commit. The user may discard a draft explicitly.

Drafts expire after 24 hours by default. Expired drafts cannot publish and may be removed by maintenance without deleting an LFS object that is referenced elsewhere or protected by the normal orphan grace period.

## Non-goals

- A browser-based text editor or semantic data editor.
- Directory archives that are expanded server-side.
- Conflict resolution, merges, rebases, or history rewriting.
- Background virus scanning or domain-specific file validation.
- Replacing Git and Git LFS clients for bulk dataset ingestion.
