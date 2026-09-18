# ADR 0011: Support Standard Git and Git LFS Workflows

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

Niyān datasets are ordinary Git repositories served through standard Git smart HTTP and Git LFS protocols. Earlier product guidance nevertheless treated direct Git and Git LFS use as an administrator escape hatch rather than a supported user workflow, requiring people to use the `niyan` CLI for normal dataset operations.

That restriction creates unnecessary adoption friction for researchers and machine-learning teams that already understand Git, use graphical Git clients, or need existing Git integrations. It also weakens the value of choosing standard repository and transfer protocols in the first place.

The Niyān CLI still provides meaningful advantages: browser-assisted authentication, safe host and dataset resolution, depth-one clones, selective materialization, automatic `.gitattributes` decisions, multipart uploads, and clearer diagnostics. Some of these conveniences cannot be made automatic for every stock Git invocation. Git cannot make a remote repository impose `--depth=1` on a clone, and Git attributes cannot select Git LFS dynamically by file size or binary content.

Authentication and authorization must remain secure regardless of which client is used. Client configuration is a convenience and cannot replace Django policy, token scopes, dataset boundaries, or server-side ref-update checks.

## Decision

Standard Git and Git LFS over HTTPS are officially supported v1 workflows. Niyān documentation recommends the `niyan` CLI as the easiest and most capable client, but does not tell experienced users that direct Git use is unsupported or reserved for recovery.

The server accepts conforming Git smart-HTTP and Git LFS clients using Niyān access tokens as HTTPS Basic-authentication passwords. Django and server-controlled Git hooks apply the same identity, dataset authorization, token scope and boundary, protected-ref, and LFS-availability policy regardless of client.

`niyan auth login` configures a host-scoped `git-credential-niyan` helper through standard Git configuration unless the user explicitly opts out. The helper retrieves the already-selected Niyān token from the package's credential store, returns nothing for unrelated origins, and honors dataset-scoped bindings by using the HTTP repository path. Access tokens never enter remote URLs or tracked repository files. Users may instead use a manually issued access token with another Git credential helper.

Niyān configures its Git LFS multipart transfer agent through supported Git LFS configuration. Stock Git LFS clients without that configuration continue to use the standard basic transfer adapter when the object store can safely accept a single upload. They may receive an actionable capability error for an object that requires multipart transfer. Reduced resilience or throughput without the recommended client is acceptable; silent corruption or Django proxying is not.

The automatic binary-or-over-10-MiB rule applies to `niyan add`. It persists its result through ordinary committed `.gitattributes` rules. A user invoking `git add` directly is responsible for their own Git LFS tracking rules, and the server does not reject an otherwise authorized push merely because an ordinary Git blob would have been classified into LFS by `niyan add`.

Depth-one, single-branch cloning remains the Niyān CLI default. Standard Git users choose their own clone, fetch, checkout, merge, and history behavior. Niyān does not attempt to force those local choices from the server.

Niyān-specific checkout metadata is optional acceleration for the CLI rather than a prerequisite for repository validity. The CLI should be able to recognize or explicitly adopt a standard clone from its remote URL and server metadata without rewriting repository history.

## Consequences

- Existing Git tools, graphical clients, submodule workflows, and automation can use Niyān repositories through familiar protocols.
- The access-token model and server authorization architecture do not need a parallel Git-specific identity system.
- The Python package must expose a real Git credential-helper entry point and carefully scoped Git configuration rather than injecting credentials only into child processes.
- Documentation and the web clone interface must show both recommended Niyān commands and supported standard Git commands.
- Standard Git users can create larger repositories, download more history, or make less efficient LFS choices than the Niyān CLI defaults. Those are informed client choices rather than server policy violations.
- Very large multipart uploads may require the installed and configured Niyān transfer agent even when the user otherwise invokes only Git commands.
- Tests must cover both CLI-mediated workflows and direct stock Git/Git LFS authentication, clone, fetch, push, and transfer behavior.
