# Niyān Authentication

- **Status:** Accepted
- **Audience:** server, web, CLI, Python client, and deployment maintainers
- **Last reviewed:** 2026-09-17

## Purpose

This specification defines how people and programs authenticate to a Niyān installation. Authentication establishes an actor and a credential's permitted surfaces; authorization separately determines what that actor may do to a particular namespace or dataset.

The effective permission for every request is the intersection of the authenticated user's current permissions and the credential's scopes. A token must never grant an operation that its owning user could not perform interactively.

## Supported Authentication Modes

### Browser Sessions

The web application uses Django session authentication. Production session cookies must be secure, HTTP-only, and protected by Django's CSRF controls for state-changing requests. A browser session may call the versioned REST API on behalf of the signed-in user.

Account passwords must not authenticate Git, Git LFS, CLI API requests, or Python-client requests.

### Interactive CLI Login

`niyan auth login <hostname>` uses a browser-assisted device flow. The CLI requests a short-lived device authorization from the selected installation and receives a verification URL, a human-readable one-time code, a private device code, an expiry, and a polling interval.

The CLI should open the verification URL when possible and must also print it for remote shells and headless login nodes. The user signs in through the web application, reviews the requesting CLI, scopes, and resource boundary, and approves or denies it. The CLI polls using the private device code and receives an opaque access token only after approval.

By default, `niyan auth login <hostname>` requests a user-level token. It can act across every dataset the user can currently access, subject to its scopes and the user's current Django-backed authorization. This default optimizes for authenticating once and then using the whole installation without repeated prompts.

`niyan auth login <hostname> --dataset <namespace/dataset>` requests a token restricted to exactly one dataset. The server resolves the human-facing path during approval and persists the immutable dataset UUID as the boundary. Renaming or moving the dataset must not broaden or break the boundary, while deleting the dataset makes the token unusable.

`niyan auth login <hostname> --local` limits local credential selection to the current checkout. It stores only a non-secret token binding in checkout-local Niyān configuration while keeping the secret in the credential store. `--local` does not narrow the server-side resource boundary; it may be combined with `--dataset` when both local selection and dataset-restricted authorization are desired.

The initial CLI flow may issue an expiring access token without refresh-token support. An expired token requires another login. Device and user codes must be single-use, short-lived, resistant to guessing, and invalidated after successful exchange or denial.

### Manually Issued Access Tokens

Users may also create access tokens manually for automation, scheduled jobs, HPC environments, and tools that cannot complete an interactive browser flow. Interactive and manually issued credentials use one access-token model, format, validation mechanism, and permission system. Their issuance origin is audit metadata rather than a distinct credential type.

A manually issued token has a user-provided name, selected scopes, a user-level or single-dataset resource boundary, an expiry, creation and last-used timestamps, and revocation state. The complete token value is returned only once when it is created. The server stores a cryptographic digest rather than the recoverable secret. Listing tokens returns metadata and a non-secret identifying prefix, never the credential itself.

Token creation, listing, and revocation of another token initially require a browser-authenticated session. A bearer token may revoke itself so `niyan auth logout` can complete the credential lifecycle, but it must not create, list, inspect, or revoke any other token. A user may supply an existing access token to the CLI through standard input, and automation may provide one through an environment variable. Tokens must not be accepted as command-line arguments or embedded in dataset URLs.

## Resource Boundaries

Every access token has exactly one resource boundary:

- `user`: the token may act on any current or future dataset the owning user is authorized to access; or
- `dataset`: the token may act only on one immutable dataset UUID.

The effective permission for an operation is the intersection of the user's current permissions, the token's operation scopes, and its resource boundary. Resource restrictions must be enforced by the server; local CLI mappings are only a credential-selection aid and are not an authorization control.

Dataset-bound tokens must not access unrelated dataset resources even when the owning user can. User-level tokens must not preserve access that the user later loses. Namespace-level boundaries and tokens spanning an explicit allowlist of several datasets are outside v1.

## Token Scopes

The initial scope vocabulary is:

- `read_api`: call read-only REST operations that are not repository-content operations;
- `api`: call read and write REST operations, subject to the user's authorization;
- `read_repository`: resolve, clone, fetch, browse, and download Git and Git LFS content for authorized datasets; and
- `write_repository`: perform `read_repository` operations and push Git and Git LFS content for authorized datasets.

`api` includes `read_api`, and `write_repository` includes `read_repository`. Repository-content and transfer endpoints may accept repository scopes without requiring a second API scope so that the Python filesystem client can operate with `read_repository` alone.

Interactive CLI login requests `api` and `write_repository` by default so one login supports the complete CLI workflow. A read-only login requests `read_api` and `read_repository` instead. The approval screen must display the requested scopes before issuance.

Scope checks are necessary but never sufficient: dataset visibility, namespace membership, role, protected-ref policy, and other authorization rules still apply.

## REST Authentication

Non-browser REST clients send access tokens using the standard header:

```http
Authorization: Bearer <token>
```

Tokens must not be accepted in query parameters. Authentication failures use a stable unauthorized response without revealing whether a token selector, user, or token secret was valid. Revocation and expiry must take effect across REST, Git, and Git LFS consistently.

## Git and Git LFS Authentication

Git over HTTPS is the only Git transport supported in v1. SSH Git transport is outside the initial product.

Users are not expected to invoke Git or Git LFS directly. For operations such as dataset clone, add, update, commit, and synchronization, the `niyan` CLI invokes those tools internally with a temporary, host-specific credential-helper configuration. It must not write a token into a remote URL, `.gitmodules`, repository configuration, command-line argument, or process-visible environment intended for the child Git command.

When an internally invoked Git or Git LFS process requests credentials, Niyān's credential helper returns a username and the stored access token as the HTTPS Basic-authentication password. Read operations require `read_repository`; pushes and LFS uploads require `write_repository`. Django authenticates and authorizes the request before invoking Git's smart-HTTP backend or issuing an LFS transfer action.

The CLI should inject its credential helper only into Git processes it launches rather than installing a global helper automatically. This keeps direct Git CLI usage outside the supported workflow while preserving standard protocol compatibility.

## Local Credential Storage

CLI configuration stores hostnames, account identifiers, token identifiers, resource mappings, and non-secret preferences separately from credentials. Access-token secrets should be stored in the operating system credential store when one is available.

The CLI may store multiple tokens for one installation and account. A dataset-bound token explicitly associated with the target dataset takes precedence over a user-level token. If that dataset token is invalid, expired, or revoked, the CLI must report the credential failure and must not silently fall back to a broader user-level token. Selecting or replacing the broader credential requires an explicit user action.

An environment variable may override stored credentials for non-interactive operation. On headless Linux systems without a credential store, the CLI may offer a mode-`0600` plaintext credential file only through an explicit `--insecure-storage` choice with a warning. It must never silently downgrade from secure storage to plaintext.

Credentials are scoped by installation hostname and account. Commands must not send a credential to a different hostname after redirects or configuration changes.

The Python filesystem client authenticates with an explicitly supplied access token or another supported headless credential and sends it as a bearer token. Because the filesystem client and CLI share one Python distribution, they may use the same internal credential-discovery and storage contract. Filesystem code must not import CLI user-interface modules, invoke the `niyan` console entry point, or perform credential discovery merely because it was imported.

The public environment-variable contract is `NIYAN_HOST` for the selected installation and `NIYAN_TOKEN` for an explicitly supplied access token. Environment credentials take precedence over stored credentials for that process.

## Token Lifecycle and Audit Metadata

Tokens must support explicit revocation and expiration. `niyan auth logout <hostname>` should revoke the active CLI access token on the server and remove its local secret; a separate, explicit local-only option may remove an unavailable server's credential without pretending it was revoked.

The server records token creation, last use, expiry, and revocation metadata without logging token values. Updating last-used metadata must not expose secrets or require unsafe logging of authorization headers.

The user's dashboard lists every access token together, regardless of whether it was issued through CLI login or created manually. Each entry should show its name or device label, issuance origin, non-secret fingerprint, resource boundary, scopes, creation time, last use, expiry, and revocation state, and must provide a direct revocation action.

All access tokens use the `niyan_` prefix. The remaining token format must preserve enough untrusted-input validation and indexed lookup information without weakening secret entropy.

Access tokens expire after at most 365 days in v1. Installations may configure a shorter maximum. When the caller does not select an earlier expiry, the server uses its configured maximum.

## Initial API Surface

The versioned REST API provides the following authentication operations:

- list the signed-in user's access-token metadata;
- create a manually issued access token and return its complete secret once;
- revoke one of the signed-in user's access tokens;
- start a device authorization and return device and user codes;
- inspect and approve or deny a device authorization using a browser session;
- poll a device authorization and exchange an approved device code for its complete access token once; and
- inspect the currently authenticated user and access-token metadata.

Token-management and device-approval operations require a browser-authenticated session, except that an active bearer token may revoke itself. Device initiation and polling are unauthenticated but rate-limited. Polling distinguishes pending, denied, expired, and successful authorization without revealing user information before success.

The access-token secret returned by manual creation or device exchange is never returned again. Token listing and current-credential inspection return only metadata and a non-secret fingerprint.

## Transport Security

Production installations must use TLS for authenticated web, REST, Git, Git LFS, and device-flow traffic. Signed transfer URLs and access tokens are bearer credentials and must never be logged. Authentication endpoints require rate limiting and must use constant-time secret comparison after locating a candidate token.

Redirects must not forward authorization headers or Git credentials to another origin. Error responses must not reveal token digests, raw credentials, session identifiers, device codes, signed URLs, or backend authentication details.

## Initial Non-goals

- Git over SSH.
- Authenticating Git with an account password.
- Third-party OAuth applications.
- Service accounts, project tokens, or namespace tokens.
- LDAP, SAML, OIDC, or institutional single sign-on.
- Refresh-token rotation.
- Namespace-level token boundaries or one token spanning an explicit allowlist of multiple datasets.

The initial CLI may offer a mode-`0600` plaintext credential file only through an explicit `--insecure-storage` choice with a warning; it must never select that fallback automatically. The filesystem client must support an explicitly supplied token for headless environments and may reuse a stored credential only through the package's documented shared credential contract.
