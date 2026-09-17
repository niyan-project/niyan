# Niyān CLI

- **Status:** Draft
- **Audience:** CLI, server, and release maintainers
- **Last reviewed:** not yet reviewed

## Purpose

The standalone `niyan` CLI is the supported user interface for creating, consuming, and versioning dataset repositories. Users should not need to invoke Git or Git LFS directly inside a Niyān dataset checkout. The CLI delegates repository mechanics to those standard tools while owning authentication, remote resolution, large-file coordination, safe defaults, and user-facing diagnostics.

The CLI consumes only documented REST, Git smart-HTTP, and Git LFS interfaces. It must not import Django server code. A parent research or machine-learning repository remains an ordinary Git repository; the requirement to use Niyān commands applies to dataset repositories managed by Niyān.

## Implementation and Distribution

The v1 CLI is implemented in Python 3.11 or newer as the `niyan` console entry point of the unified `niyan` PyPI distribution. The same distribution exposes the read-only Python filesystem client. Python is an implementation choice rather than part of the command-line compatibility contract. The primary installation experience must not require users to create or activate a virtual environment themselves.

The CLI and filesystem client remain separate internal modules. They may share narrowly scoped HTTP, authentication, configuration, models, errors, and transfer primitives. Importing or using the filesystem client must not initialize CLI state, inspect a Git checkout, invoke Git or Git LFS, access a credential store, or perform network I/O unless the requested filesystem operation requires it.

The primary installation path must be a project-controlled `curl -fsSL ... | sh` bootstrap installer. The installer detects the operating system and architecture, downloads a versioned native executable archive, verifies it against the release's published SHA-256 checksum before installation, and installs `niyan` into `~/.local/bin` by default. It must never invoke `sudo` automatically. `NIYAN_INSTALL_DIR` may select another installation directory and `NIYAN_VERSION` may select an exact CLI version.

Release artifacts must initially cover supported Unix-compatible Linux and macOS architectures. The same checksummed archives must be downloadable manually for offline or inspected installation. Re-running the installer is the v1 upgrade mechanism. Package-manager formulae and a self-update command may be added later without changing the CLI contract.

`pipx install niyan` is the standard isolated installation from PyPI. Installing `niyan` into a project environment through pip or uv provides both the importable filesystem client and the environment-local console entry point. The bootstrap installer and PyPI installation must expose the same CLI behavior and version.

Git and Git LFS remain external dependencies for repository-oriented CLI commands. The installer must not modify the system package manager or install them implicitly; it reports missing dependencies and points to installation instructions, while `niyan doctor` reports missing or unsupported versions after installation. Contributors use uv and checked-in lock data.

## Command Structure

The canonical v1 command hierarchy is:

```text
niyan
├── auth
│   ├── login
│   ├── logout
│   └── status
├── dataset
│   ├── create
│   ├── clone
│   ├── list
│   ├── view
│   ├── edit
│   ├── delete
│   ├── tree
│   ├── cat
│   ├── download
│   ├── add
│   ├── remove
│   ├── update
│   ├── status
│   └── access
│       ├── list
│       ├── grant
│       ├── edit
│       └── revoke
├── status
├── add
├── restore
├── commit
├── diff
├── log
├── branch
│   ├── list
│   ├── create
│   ├── rename
│   └── delete
├── switch
├── merge
├── tag
│   ├── list
│   ├── create
│   └── delete
├── fetch
├── pull
├── push
├── cache
│   ├── status
│   └── prune
├── browse
├── api
├── config
│   ├── list
│   ├── get
│   ├── set
│   └── unset
├── doctor
├── completion
└── version
```

Canonical documentation uses the complete command names. Short aliases such as `ls` and `rm` are not part of the initial compatibility contract.

## Context and Selection

Commands that accept a dataset use the human-facing `namespace/dataset` path. Nested namespace paths are allowed. When the dataset argument is omitted, the CLI may infer the immutable dataset UUID and host from the current Niyān checkout. It must reject ambiguous or inconsistent remotes rather than guessing.

Host selection order is an explicit command option, `NIYAN_HOST`, the current checkout's configured default, then the user's configured default. Host URLs require HTTPS except for loopback development servers. Redirects must never cause a credential to be sent to another origin.

`NIYAN_TOKEN` supplies an explicit process-local credential and takes precedence over stored credentials. Tokens must never be accepted as command-line arguments.

## Authentication

The authentication surface is:

```text
niyan auth login [hostname]
niyan auth login [hostname] --local
niyan auth login [hostname] --dataset <namespace/dataset>
niyan auth login [hostname] --read-only
niyan auth login [hostname] --with-token
niyan auth status [--host <host>] [--dataset <namespace/dataset>]
niyan auth logout [--host <host>] [--local] [--dataset <namespace/dataset>] [--forget]
```

`auth login` uses the browser-assisted device flow by default. It opens the verification page when possible, prints the verification URL and user code for headless environments, respects the server-provided polling interval, and stores a token only after approval.

The default login requests `api` and `write_repository`. `--read-only` requests `read_api` and `read_repository`. `--dataset` requests a server-enforced single-dataset boundary. `--with-token` reads one manually issued token from standard input; it must not accept the secret from an argument or echo it.

Without `--local`, the token binding is available from any working directory. With `--local`, the non-secret binding is stored beneath the current Git checkout and is not selected outside it. Secrets use the operating-system credential store. An explicit `--insecure-storage` option may use a mode-`0600` plaintext user data file after warning the user; the CLI must never choose this fallback automatically.

Credential selection order is checkout-local exact dataset binding, user-configured exact dataset binding, checkout-local user-level binding, then user-configured user-level binding. A missing, rejected, expired, or revoked selected credential is reported without silently falling back to a broader credential.

`auth status` reports the selected host, account, binding scope, token scopes, expiry, storage method, and whether the server accepts the credential. It never displays the token. `--dataset` selects an exact dataset-bound credential before a broader user credential.

`auth logout` targets user configuration by default; `--local` targets the current checkout's configuration. `--dataset` targets an exact dataset binding instead of that configuration's user-level binding. Logout revokes the selected token before removing its binding and secret. `--forget` removes the selected binding and secret without server revocation after an explicit warning; it exists only for recovery when the server is unreachable and must not claim that revocation succeeded.

## Dataset Forge Operations

The remote dataset management surface is:

```text
niyan dataset create [<namespace/dataset>]
niyan dataset create <namespace/dataset> --source <directory>
niyan dataset create <namespace/dataset> --clone [--full-history]
niyan dataset clone <namespace/dataset> [<directory>] [--full-history]
niyan dataset list [<namespace>]
niyan dataset view [<namespace/dataset>] [--web]
niyan dataset edit [<namespace/dataset>]
niyan dataset delete [<namespace/dataset>]
```

`dataset create` creates an empty remote dataset. With no path, an interactive terminal may prompt for the namespace, slug, and display name. `--clone` immediately creates its local checkout. `--source` turns an existing directory into the initial dataset working copy, applies the accepted LFS tracking policy, creates an initial commit after showing the planned changes, and pushes it. It must refuse a source directory whose existing Git state would be overwritten or ambiguously repurposed.

`dataset clone` resolves the mutable path to an immutable UUID, invokes Git smart HTTP with a temporary Niyān credential helper, and configures checkout-private Niyān metadata. The metadata records the installation host, immutable dataset UUID, last-known canonical path, remote name, and history policy beneath the checkout's private Git directory. It must not place credentials in the URL, command arguments, child environment, `.git/config`, or `.gitmodules`. Commands inside the checkout resolve the dataset by UUID, verify that the configured remote still targets that UUID on the recorded host, and refresh a stale canonical path after a server-side rename.

Dataset clones are shallow, single-branch, depth-one clones without tags by default. `--full-history` requests ordinary complete Git history and remote refs for the exceptional workflow that needs them. Later fetch and pull operations preserve the checkout's shallow history policy unless the user explicitly requests full history. Fetching another branch may retain commits required by local branches, commits, stashes, or unpushed work; Niyān must never discard local-only state merely to reduce disk usage.

Depth limits reachable Git history rather than guaranteeing that every older Git object is immediately absent from disk. Normal safe Git maintenance may eventually reclaim unreachable objects, but Niyān must not force-prune ordinary Git objects merely to simulate a one-version cache.

Shallow Git history and local Git LFS storage are separate concerns. The default LFS workflow materializes the selected working set for the checked-out commit and may reclaim older local LFS objects only after protecting the current checkout, stashes, other worktrees, and unpushed content and verifying reachable deletion candidates against the remote. The checkout metadata records this policy, but automatic LFS materialization and reclamation begin with the LFS transfer stage.

`dataset list` returns only datasets currently visible to the user. `dataset view` prints identity, namespace, default branch, access role, and README summary; `--web` opens the corresponding web page. `dataset edit` changes supported mutable metadata such as display name and slug.

`dataset delete` permanently deletes the control-plane record, Git repository, and dataset-owned LFS objects once that data plane exists. An interactive deletion requires the exact dataset path to be typed. Non-interactive deletion requires an explicit `--confirm <namespace/dataset>` value; a generic `--yes` is insufficient.

## Remote File Access

Remote inspection and retrieval without a checkout use:

```text
niyan dataset tree <namespace/dataset> [<path>] [--ref <revision>]
niyan dataset cat <namespace/dataset> <path> [--ref <revision>]
niyan dataset download <namespace/dataset> [<paths>...] [--ref <revision>] [--output <directory>]
```

`dataset tree` lists direct children at a requested revision. `dataset cat` streams exactly one file to standard output and never adds progress text to that stream. `dataset download` retrieves selected files or directories without creating a Git checkout and writes through temporary files before atomically finalizing complete downloads.

Every operation resolves the requested branch or tag to an exact commit before reading content. Recursive downloads remain pinned to that commit for their entire duration. LFS content transfers directly from object storage through authorized URLs; ordinary Git blobs use the repository API. Interrupted downloads must not be presented as complete files.

## Project Dataset Dependencies

A normal research or machine-learning Git repository manages pinned dataset dependencies with:

```text
niyan dataset add <namespace/dataset> [<path>] [--ref <revision>]
niyan dataset remove <path>
niyan dataset update [<path>] [--ref <revision>]
niyan dataset status
```

These commands manage standard Git submodules. `dataset add` resolves and checks out the dataset, records the standard Git URL, and stages the parent repository's `.gitmodules` and gitlink changes without committing the parent repository. It must refuse to add a dataset inside another Niyān dataset checkout.

`dataset remove` removes the selected dataset dependency and stages the corresponding parent-repository changes without committing them. `dataset update` resolves an explicit revision or updates according to the dependency's configured branch, checks out an exact commit, and stages the changed gitlink. `dataset status` shows each attached dataset, path, host, immutable UUID, pinned commit, configured moving ref if any, checkout state, and unavailable LFS content.

The parent project remains an ordinary Git repository, so its user may commit these dependency changes with their normal Git workflow. Niyān never stores credentials in `.gitmodules`.

## Dataset Working-copy Operations

Inside a Niyān dataset checkout, the supported everyday version-control surface is:

```text
niyan status
niyan add <paths>...
niyan add --all
niyan restore <paths>...
niyan restore --staged <paths>...
niyan commit [-m <message>]
niyan diff [--staged]
niyan log [--limit <count>]
```

These commands first validate the checkout's stored immutable dataset identity against its configured remote, then delegate to Git. `status` uses Git's stable porcelain-v2 interface to distinguish staged changes, unstaged changes, additions, deletions, renames, untracked files, and conflicts. It reports whether history is shallow and classifies indexed LFS paths as materialized, missing, locally cached pointer content, or unavailable pointer content without contacting the server or requiring Git LFS. Ordinary files are never parsed as dataset formats.

`add` stages additions, changes, renames, and deletions while applying the accepted LFS tracking policy. Explicit `--lfs` and `--git` overrides may be provided, but the CLI must not silently rewrite existing history or migrate an already tracked file between storage modes merely because its size changed.

`restore` requires one or more explicit paths and affects only those paths. Without `--staged`, it restores working-tree content from the index. With `--staged`, it removes the selected changes from the index while preserving their working-tree content. It does not expose Git's destructive source-selection options in v1.

`commit` creates an ordinary local Git commit from staged changes and never pushes implicitly. `-m` or `--message` supplies its message; when omitted, Git opens the user's configured editor. Niyān reports an empty index before starting the commit flow, while Git remains responsible for hooks, identity configuration, signing configuration, and commit construction. `diff` streams Git's structural or textual diff and does not promise semantic comparison of dataset formats; `--staged` compares the index to `HEAD`. `log` shows at most 20 commits by default, accepts a positive `--limit`, handles an unborn repository, and reports when the visible history is shallow. Machine-readable output is a later extension rather than part of this initial surface.

## Branches, Tags, and Merging

Niyān must expose ordinary branch, merge, and tag workflows so users do not need the Git CLI for normal dataset version control:

```text
niyan branch list
niyan branch create <name> [<start-point>]
niyan branch rename <old> <new>
niyan branch delete <name>
niyan switch <branch>
niyan switch --create <branch> [<start-point>]
niyan merge <branch-or-commit>
niyan tag list
niyan tag create <name> [-m <message>]
niyan tag delete <name>
```

Branch and tag mutations remain subject to server policy when pushed. Tag creation produces an annotated tag by default. Merging delegates tree and object semantics to Git. Niyān may explain conflicts but must not attempt semantic merges of CSV, Parquet, images, medical data, or other formats. Competing changes to an LFS-backed file are ordinary whole-file conflicts.

Destructive branch or tag deletion requires confirmation when an interactive terminal is available. V1 does not expose unrestricted force deletion of remote refs.

## Synchronization and Selective Materialization

Repository synchronization uses:

```text
niyan fetch
niyan pull [--full-history]
niyan push
```

`fetch` updates remote Git refs and metadata without modifying the working tree or downloading LFS objects by default. `pull` fetches and performs a fast-forward update of the current branch, then materializes the configured LFS working set. In a shallow checkout it keeps the current branch at depth one by default; `--full-history` converts the checkout to complete history. A divergent branch is reported rather than silently selecting merge or rebase; the user may run `niyan merge` explicitly.

`push` uploads required LFS objects, completes server-required verification, and only then proposes Git ref updates. A failed object upload or verification must prevent the corresponding ref update from becoming visible. Pushes use expected-old-object concurrency and report non-fast-forward rejection without silently overwriting remote work.

Clone and pull support repeatable `--include <glob>` and `--exclude <glob>` filters for large-file materialization. `--metadata-only` checks out Git metadata and pointer state without downloading LFS object bytes. The selected working set is recorded as non-secret checkout configuration so later pulls behave consistently.

V1 does not offer plain `--force`. A future `--force-with-lease` may be added only with server branch-policy enforcement.

## Dataset Access Management

Dataset owners manage explicit grants with:

```text
niyan dataset access list [<namespace/dataset>]
niyan dataset access grant [<namespace/dataset>] (--user <username> | --group <namespace>) --role <role>
niyan dataset access edit [<namespace/dataset>] (--user <username> | --group <namespace>) --role <role>
niyan dataset access revoke [<namespace/dataset>] (--user <username> | --group <namespace>)
```

The CLI resolves human-facing usernames and Niyān group paths to immutable identities. It never asks users to manipulate database primary keys. Roles are `reader`, `contributor`, `maintainer`, and `owner`. Access commands display the effective consequences of a grant but do not duplicate authorization policy locally; the server remains authoritative.

## Local Cache

Git LFS cache inspection and safe reclamation use:

```text
niyan cache status
niyan cache prune [--dry-run]
```

`cache status` reports local cache location, total size, reachable content, and reclaimable content. `cache prune` must preserve objects referenced by the current checkout and Git LFS's accepted recent-reference safety window. Interactive pruning shows the proposed reclaimed size and requests confirmation; `--dry-run` never mutates the cache.

Git LFS remains an internal dependency. There is no public `niyan lfs` command group.

## Utilities

The general utility surface is:

```text
niyan browse [<path>] [--ref <revision>]
niyan api <endpoint>
niyan config list
niyan config get <key>
niyan config set <key> <value>
niyan config unset <key>
niyan doctor
niyan completion <bash|zsh|fish>
niyan version
```

`browse` opens the current dataset, revision, or path in the web application. `api` makes an authenticated request to the selected installation and supports an explicit method, request fields, a body from a file or standard input, response headers, and pagination. It must enforce the same-origin credential rule as every other CLI request.

`config` manages documented non-secret preferences and host bindings. It must not print or edit access-token secrets. Unknown keys are rejected rather than silently persisted.

`doctor` is read-only. It checks Git and Git LFS availability and versions, server reachability, authentication, token scopes, credential storage, current checkout identity, expected remotes, relevant Git/LFS configuration, missing required LFS objects, and obvious repository inconsistencies. It prints actionable diagnoses without attempting repair.

`completion` emits a shell completion script to standard output. `version` and the root-level `--version` flag report the CLI version and may include build metadata in structured output.

## Output and Automation

Human-readable output is the default. Commands that return structured records support `--json <fields>` and `--jq <expression>`. Commands that emit requested file content, shell completion, or raw API bodies reserve standard output exclusively for that content. Progress, warnings, prompts, and diagnostics go to standard error.

Applicable commands support `--host`, `--quiet`, and `--no-color`. List operations use bounded defaults and explicit limits or pagination. JSON field names and documented exit statuses are public compatibility surfaces.

Interactive prompts are allowed only when standard input is a terminal. Non-interactive use must either provide all required values through arguments, safe environment variables, or standard input, or fail with an actionable message. Secrets, signed URLs, credential-helper output, and authorization headers must never appear in logs or diagnostics.

The initial stable exit-status categories are:

| Status | Meaning |
| --- | --- |
| `0` | Success. |
| `1` | General operational failure without a more specific category. |
| `2` | Command syntax or local input validation failure. |
| `3` | Authentication or credential-storage failure. |
| `4` | Authenticated but not authorized. |
| `5` | Requested host resource, revision, or path not found. |
| `6` | Conflict, dirty-state refusal, or non-fast-forward update. |
| `7` | Network, server, or storage service unavailable. |
| `8` | Missing or incompatible local dependency or invalid checkout. |
| `130` | Interrupted by the user. |

Commands may provide a more specific machine-readable error code in JSON output while preserving these process-level categories.

## Security and Delegation

The CLI injects its credential helper only into child Git and Git LFS processes it launches. It never installs a global Git credential helper automatically. The helper verifies protocol and host before returning a credential, and server-side token scopes and resource boundaries remain authoritative.

Git and Git LFS subprocesses receive the minimum environment required for the operation. Niyān must validate server-provided clone and transfer URLs before invoking another process or making an authenticated request. User-controlled paths and revisions are passed without a shell.

Temporary downloads and credential handoffs use private permissions and are removed after use. The CLI must handle interruption without leaving a file that appears complete, a credential in repository configuration, or an unreported partially applied parent-project dependency change.

## V1 Non-goals

- Windows support.
- Git over SSH.
- Rebase, cherry-pick, bisect, reflog, stash, arbitrary reset, or history-filtering interfaces.
- Plain force-push or silent history rewriting.
- Raw Git LFS commands.
- Semantic file diffs or merges.
- CLI aliases or third-party CLI extensions.
- Multiple simultaneously active accounts on one host.
- User, group, or installation administration beyond dataset access grants.
- Release objects distinct from standard Git tags.
- Existing-S3-prefix import, repository mirroring, or dataset export.
- A self-update command or automatic background updates.
- Automatic fallback to plaintext credential storage.
