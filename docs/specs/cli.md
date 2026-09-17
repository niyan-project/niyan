# Niyān CLI

- **Status:** Draft
- **Audience:** CLI, server, and release maintainers
- **Last reviewed:** not yet reviewed

## Purpose

The standalone `niyan` CLI is the supported user interface for dataset repository workflows. It invokes Git internally while keeping authentication, remote resolution, and future Git LFS coordination behind Niyān commands.

The initial implementation is an independently packaged Python application under `clients/cli`. It must not import Django server code. A future bootstrap installer may distribute an isolated environment or executable artifact without changing the public command contract.

## Host Selection

`niyan auth login [hostname]` selects an installation explicitly, from `NIYAN_HOST`, from the current checkout's configured default, or from the user's configured default in that order. Other commands use the same host selection order. Host URLs must use HTTPS except for loopback development hosts. Redirects must never cause credentials to be sent to another origin.

## Login

`niyan auth login` starts the browser-assisted device flow, opens the verification page when possible, prints the URL and user code for headless environments, polls at the server-provided interval, and stores the resulting access token only after approval.

The default login requests `api` and `write_repository`. `--read-only` requests `read_api` and `read_repository`. `--dataset <namespace/dataset>` requests a dataset-bound token.

Without `--local`, the token binding is stored in the user's Niyān configuration and may be selected from any working directory. With `--local`, the binding is stored beneath the current Git repository's private `.git/niyan` directory and is not selected outside that working tree. The secret itself remains in the operating-system credential store.

An explicit `--insecure-storage` option may place secrets in a mode-`0600` user data file when a credential-store backend is unavailable. The CLI never chooses this fallback automatically.

Environment credentials supplied through `NIYAN_TOKEN` override stored credentials for that process. Tokens are never accepted as command-line arguments.

## Credential Selection

The CLI may retain several credentials for one host. Selection order is checkout-local exact dataset binding, user-configured exact dataset binding, checkout-local user-level binding, then user-configured user-level binding. This makes a dataset-specific restriction more important than storage location while still preferring checkout-local configuration between bindings of equal specificity. If the selected local or dataset credential is missing, rejected, expired, or revoked, the CLI reports that failure and does not silently fall back to a broader credential.

Configuration contains host URLs, usernames, token UUIDs, dataset UUIDs or paths, storage method, and other non-secret metadata. Token values are stored separately.

## Dataset Clone

`niyan dataset clone <namespace/dataset> [destination]`:

1. selects a credential for the host and requested dataset path;
2. resolves the path through the public REST API;
3. obtains the immutable dataset UUID and smart-HTTP clone URL;
4. invokes `git clone` without embedding the token in the URL, arguments, environment, or repository configuration; and
5. supplies credentials only through a temporary Niyān Git credential helper.

The initial clone downloads Git-resident content. Git LFS checkout behavior is deferred until the LFS slice and must not silently replace unavailable large objects with misleading content.

The helper verifies the requested protocol and host before returning a username and token password. Niyān injects it only into Git child processes launched by Niyān and never installs it globally.

## Output and Errors

Human progress and diagnostics go to stderr. Requested machine-consumable output goes to stdout. Authentication denial, unknown datasets, missing Git, unavailable credential storage, and Git subprocess failure produce distinct actionable messages and non-zero exit status without printing secrets.

## Initial Non-goals

- Push, commit, add, update, or submodule orchestration.
- Git LFS transfer.
- Windows support.
- Shell completion and machine-readable JSON output.
- Automatic plaintext credential fallback.
