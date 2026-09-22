---
title: Using Niyān
description: Learn how to use a Niyān server for everyday dataset work.
---

# Using Niyān

This guide is for researchers, machine-learning engineers, and other members of an organization that already runs a Niyān server. There are two ways to work with it.

<div class="niyan-audience-grid">
  <a class="niyan-audience-card" href="/users/cli/">
    <span class="niyan-audience-label">Local and automated work</span>
    <strong>The niyan CLI</strong>
    <span>Authenticate, clone datasets, version local work, selectively download data, and use the Python client.</span>
  </a>
  <a class="niyan-audience-card" href="/users/dashboard/">
    <span class="niyan-audience-label">Browser-based work</span>
    <strong>The user dashboard</strong>
    <span>Create and browse datasets, publish browser commits, manage groups and access, and control account security.</span>
  </a>
</div>

Niyān datasets are Git repositories whose large files are stored with Git LFS in S3-compatible object storage. Both surfaces operate on the same datasets, permissions, branches, commits, and access tokens.

## Which should I use?

- Use [the dashboard](/users/dashboard/) for discovery, occasional uploads, account settings, group membership, and access management.
- Use [the CLI](/users/cli/) for working copies, directory trees, branches and tags, repeatable workflows, large-scale transfers, and project dependencies.
- Use the Python client included with the CLI package to [stream files with fsspec](/users/python-and-fsspec).

If you prefer ordinary Git commands, read [Standard Git and Git LFS](/users/standard-git) for the differences you should expect.

## What your administrator provides

You need:

- the full HTTPS URL of your Niyān server, such as `https://data.example.edu`;
- a Niyān user account; and
- access to at least one group or dataset.

Niyān does not support public registration. If you cannot sign in or cannot see the dataset you need, contact the administrator for your installation.
