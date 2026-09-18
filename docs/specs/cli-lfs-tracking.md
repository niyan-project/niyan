# Niyān CLI Git LFS Tracking Policy

- **Status:** Accepted
- **Audience:** CLI, Git LFS, and testing maintainers
- **Last reviewed:** 2026-09-18

## Purpose

This specification defines how `niyan add` chooses between ordinary Git blobs and Git LFS for dataset files. The policy must be deterministic, visible in standard `.gitattributes` files, compatible with stock Git LFS, and stable across later edits.

This is a Niyān CLI convenience policy, not a server acceptance rule. A user who invokes `git add` or edits `.gitattributes` directly owns that choice. The server does not reject an otherwise valid and authorized ref update merely because a Git blob would have been classified into LFS by this policy.

Git LFS does not provide a dynamic size-threshold tracking mode. Tracking is selected through Git attributes, so Niyān must persist every automatic or explicit LFS decision that was not already expressed by an applicable attribute rule. Niyān configures and invokes stock Git and Git LFS rather than replacing their clean, smudge, staging, or pre-push machinery. This specification follows [ADR 0003](../architecture/decisions/0003-git-lfs-s3-data-plane.md) and the upstream [git-lfs-track](https://github.com/git-lfs/git-lfs/blob/main/docs/man/git-lfs-track.adoc) behavior.

## Default Classification

For a newly added regular file whose Git LFS filter is not already decided by `.gitattributes`, Niyān uses Git LFS when either condition is true:

- the file is binary according to the binary-sniffing rule below; or
- the file is larger than 10 MiB, meaning greater than 10,485,760 bytes.

A new regular file that satisfies neither condition is stored as an ordinary Git blob. Empty files are ordinary Git blobs unless an explicit attribute rule or `--lfs` selects Git LFS.

The policy is format-agnostic. It does not maintain extension lists, MIME-type tables, viewer knowledge, or special rules for CSV, JSON, Parquet, images, archives, medical files, or other dataset formats.

### Binary sniffing

Niyān reads at most the first 8,000 bytes of an otherwise unclassified regular file. The file is binary when that sample contains at least one NUL byte. This deliberately mirrors Git's `buffer_is_binary` heuristic and requires no optional detection library.

Only the first 8,000 bytes participate. A file with no NUL in that sample is considered text for this default decision even if an application would describe it as binary. Conversely, encodings such as UTF-16 may be classified as binary because their sample contains NUL bytes. Users resolve misclassification through `.gitattributes`, `--lfs`, or `--git`; Niyān must not introduce machine-dependent locale or MIME guesses.

Files already selected by size need not be read for binary detection. Classification must stream or sample content and must never load a complete file merely to choose its storage mode.

## Decision Precedence

`niyan add` decides storage in this order:

1. an explicit `--lfs` or `--git` option;
2. the effective Git `filter` attribute for the path;
3. the existing storage mode for a tracked path or an unambiguous rename of one;
4. binary sniffing or the 10 MiB threshold for a new, otherwise unclassified regular file; and
5. ordinary Git for every remaining supported entry.

`--lfs` and `--git` are mutually exclusive. An explicit option persists an appropriate attribute rule before the path is staged; it is not a one-time bypass of Git's clean filter.

An effective `filter=lfs` rule selects Git LFS regardless of size or sniffing. An explicitly unset or different filter selects ordinary Git. Niyān must query Git's effective attributes rather than implement an independent approximation of `.gitattributes` precedence.

For a tracked path with no currently decisive filter rule, Niyān preserves whether its indexed or `HEAD` representation is a valid LFS pointer or an ordinary Git blob. Crossing the size threshold, gaining or losing a NUL byte, or changing a filename extension must not silently migrate an existing path. An unambiguous rename preserves its source path's mode. A copy or ambiguous rename is classified as a new path.

An explicit option or a deliberate applicable `.gitattributes` rule may migrate the newly staged version between Git and Git LFS. It changes neither previous commits nor any other path that the rule does not match. Rewriting existing history is outside `niyan add`.

## Attribute Management

Committed `.gitattributes` files are the portable source of truth for Git LFS filtering. Niyān must not rely on `$GIT_DIR/info/attributes`, global attributes, hidden Niyān metadata, filename extensions, a custom filter driver, or an uncommitted local threshold to reproduce a staged file's storage mode on another machine.

When automatic classification selects Git LFS without an existing `filter=lfs` rule, Niyān invokes stock `git lfs track --filename` to add a literal filename rule. Metacharacters in the path must be treated as literal filename content rather than a pattern. Niyān must not infer an extension or directory glob from one file because that could silently change future files.

For an explicit file argument, `--lfs` or `--git` creates a literal rule. For an explicit directory argument, it creates a recursive rule for that directory's descendants. A Git override must disable the LFS clean filter while leaving unrelated attribute rules intact.

Niyān may place a rule in the nearest appropriate in-tree `.gitattributes` file so it can take effect under Git's normal precedence. After writing a rule, it must ask Git for the effective attributes again and fail before staging content if a higher-precedence rule still prevents the requested mode. Existing user-authored lines must not be reordered, generalized, or removed merely to simplify Niyān's output.

Any `.gitattributes` file changed or created by the operation is staged in the same `niyan add` invocation as the affected paths. The change remains visible in `niyan status` and ordinary Git history. Removing or renaming a file does not automatically delete a now-unused attribute rule; automatic cleanup could change how a future file at that path is stored.

The `.gitattributes` file itself must always remain an ordinary Git blob. Niyān rejects an explicit or inherited attempt to put it through Git LFS because Git must be able to read attributes before applying filters.

## Directory Traversal and Entry Types

`niyan add <directory>` and `niyan add --all` apply this policy independently to every candidate path selected by Git. Traversal must:

- honor Git ignore rules and the caller's explicit pathspecs;
- avoid following directory symlinks;
- stage symlinks themselves as ordinary Git symlink entries;
- preserve executable bits through Git's normal index behavior;
- ignore empty directories because Git cannot represent them;
- reject nested Git repositories instead of silently creating an embedded repository or submodule; and
- reject sockets, FIFOs, devices, and other unsupported special filesystem entries with an actionable path-specific error.

The CLI should obtain the candidate set from Git-compatible path and ignore handling rather than reimplement all Git pathspec semantics. It must process classification in bounded memory and must not build an in-memory representation of an arbitrarily large directory tree.

If any requested path cannot be classified or persisted safely, `niyan add` must fail without reporting the requested operation as wholly successful. Paths already staged before the failure must be reported clearly; a future transactional staging improvement may make the operation fully atomic.

## Explicit Overrides

`niyan add --lfs <paths>...` forces supported regular files into Git LFS and persists literal or recursive attribute rules as described above. It is appropriate for opaque, generated, or machine-readable text that the NUL heuristic would otherwise leave in Git.

`niyan add --git <paths>...` forces supported regular files into ordinary Git and persists rules that disable the LFS filter. It is appropriate for a binary-sniffed file that should remain reviewable or for a file covered by a broader LFS pattern. Within `niyan add`, this explicit choice or an applicable attribute rule is required to place a newly classified large file into ordinary Git. The CLI should warn when the choice places a file larger than 10 MiB in Git, but it must honor it. Direct standard Git staging remains outside this classifier and is not rejected by the server merely for making another choice.

Overrides do not follow symlink targets and do not convert nested repositories or special filesystem entries into supported files. Applying the already-effective mode is idempotent.

## Failure and Diagnostic Behavior

Before staging content, the CLI must report an actionable error for:

- unavailable or incompatible Git LFS when at least one selected path requires it;
- an attribute rule that cannot be written or does not become effective;
- an unreadable file or binary sample;
- a path that changes type or content unsafely during classification and staging;
- a nested repository or unsupported special entry; or
- an explicit option that conflicts with another option or protected control file.

Diagnostics should identify the affected path and whether the choice came from an explicit option, effective attributes, preserved history, binary sniffing, size, or the ordinary-Git fallback. Secrets, file contents, and raw subprocess diagnostics must not be printed.

## Initial Non-goals

- MIME detection, extension allowlists, or format-specific policy.
- A custom conditional clean filter, pre-commit hook, or wrapper around arbitrary Git invocations.
- Content-defined chunking.
- Rewriting previous commits when a rule changes.
- Automatically grouping literal decisions into broader glob patterns.
- Automatically deleting unused attribute rules.
- Tracking nested repositories as dataset entries.
- Applying filters to symlink targets or special filesystem entries.

## Acceptance Scenarios

An implementation of this specification must demonstrate at least:

- a small UTF-8 text file stored as an ordinary Git blob;
- a small file with a NUL in its first 8,000 bytes stored through Git LFS;
- a text file larger than 10 MiB stored through Git LFS;
- a file exactly 10 MiB with no NUL stored as an ordinary Git blob;
- an existing Git blob remaining in Git after growing past the threshold;
- an existing LFS path remaining in LFS after becoming small text;
- `filter=lfs`, a non-LFS filter, `--lfs`, and `--git` taking precedence in the documented order;
- literal handling of filenames containing spaces and attribute metacharacters;
- recursive directory handling that respects ignores and does not follow symlinks;
- rejection of nested repositories and unsupported special entries;
- staging of a changed `.gitattributes` file with the selected content; and
- no history rewrite when a current version changes storage mode explicitly.
