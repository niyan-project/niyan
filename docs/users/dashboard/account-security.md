---
title: Account security and access tokens
description: Change account credentials and manage tokens issued manually or by the Niyān CLI.
---

# Account security and access tokens

Open the user button in the upper-right and select **Settings**. The Security sidebar contains email, password, and access-token controls.

## Change your email or password

- **Email** changes the address associated with your account after confirming your current password.
- **Password and authentication** changes your password after checking the current password and confirmation of the new one.

Niyān does not currently use the email address for public registration or pending group invitations, but administrators may use it for account-related communication.

## Create an access token

Open **Access tokens** and select **New token**. Choose:

1. A recognizable name describing the machine or purpose, such as `HPC login node`.
2. A resource boundary: the entire account, or one dataset visible to you.
3. The minimum required scopes:

| Scope | Allows |
| --- | --- |
| **Read API** | View control-plane metadata available to your account. |
| **Manage API** | Create and update control-plane resources. |
| **Read repositories** | Clone, browse, download, and stream dataset content. |
| **Write repositories** | Push commits and Git LFS objects. |

After creation, the secret is displayed exactly once. Copy it immediately and store it in an appropriate credential manager or secret store. Niyān retains only the information needed to verify the credential and cannot show the secret again.

::: warning Bearer credential
Anyone holding an access token can exercise its scopes until it expires or is revoked. Do not put tokens in Git repositories, notebooks, shell history, shared configuration, or documentation.
:::

## Review and revoke tokens

The token list includes credentials you created manually and those issued through `niyan auth login`. Each entry shows its origin, fingerprint, resource boundary, scopes, last use, expiry, and status.

Select **Revoke** when a credential is no longer needed or looks unfamiliar. Revocation takes effect on subsequent API, Git, Git LFS, and fsspec requests using that token.

For ordinary workstation use, prefer `niyan auth login`; it completes the browser authorization flow and stores the secret in the operating system's credential store. Manually created tokens are most useful for non-interactive environments such as an HPC login node.
