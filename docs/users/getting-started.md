---
title: Get started
description: Install the Niyān client, authenticate, and find your first dataset.
---

# Get started

## Requirements

Install Python 3.11–3.13, Git, and Git LFS. Then install the Niyān package in an isolated environment:

```shell
pipx install niyan
```

If `pipx` is unavailable, follow its [installation guide](https://pipx.pypa.io/stable/installation/). Verify the tools:

```shell
niyan --version
git --version
git lfs version
```

## Sign in

Pass the complete server URL to the login command:

```shell
niyan auth login https://data.example.edu
```

The CLI opens a browser to complete authentication and stores the resulting access token in your operating system's credential store. That login is available across your account on this computer.

To limit a login to the current project directory, run:

```shell
niyan auth login https://data.example.edu --local
```

Use `niyan auth status` to inspect active logins and `niyan auth logout` to revoke one.

::: tip
Access tokens issued through the CLI and tokens created in the web application appear together under **Settings → Security → Access tokens**. See [Account security and access tokens](/users/dashboard/account-security) to review or revoke them.
:::

## Find a dataset

List the datasets visible to your account:

```shell
niyan dataset list
```

Dataset names use their group path, for example `genomics/reference-data`. Groups can be nested, so a longer path such as `biology/genomics/reference-data` is valid.

Clone one into a working directory:

```shell
niyan dataset clone genomics/reference-data
```

Niyān clones only the current branch and its latest history by default. This avoids filling a workstation with old versions of large files. Use `--full-history` only when you actually need the complete history locally.

The same server URL opens the [user dashboard](/users/dashboard/) in a browser. Dashboard sessions and CLI tokens are separate credentials but operate under the same user account and permissions.
