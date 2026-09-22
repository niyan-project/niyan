---
title: The niyan CLI
description: Use Niyān from a terminal for versioned dataset work.
---

# The `niyan` CLI

The CLI is the recommended interface for substantial dataset work. It wraps ordinary Git and Git LFS, configures Niyān authentication and large-file transfers, and chooses storage-conscious defaults without introducing a proprietary repository format.

## What it handles

- Browser-assisted login and secure credential storage.
- Shallow, single-branch clones by default.
- Git LFS tracking for binary files and files larger than 10 MiB.
- Commits, branches, tags, merges, pulls, and pushes.
- Selective or metadata-only materialization of large datasets.
- Remote tree browsing, streaming to standard output, and direct downloads.
- Dataset submodules inside research and machine-learning projects.

The same Python package also provides the read-only [fsspec integration](/users/python-and-fsspec) for streaming remote files from analysis code.

## Follow the CLI guide

1. [Install the package and authenticate](/users/getting-started).
2. [Create, clone, and version datasets](/users/dataset-workflow).
3. [Browse and retrieve only the remote data you need](/users/browse-and-download).

Standard Git and Git LFS remain officially supported. The CLI makes the preferred configuration convenient; it does not replace their repository model.
