---
title: Datasets and browser commits
description: Create datasets, upload files, and browse version history in the user dashboard.
---

# Datasets and browser commits

## Create a dataset

Select **New dataset** from the main Datasets page, or open a group and select **New dataset** there. Choose the owning group or personal account, then enter a name, path slug, and optional description.

The button appears only where your role permits dataset creation. A newly created dataset is an empty Git repository.

## Upload and commit files

Open the dataset's **Files** tab and select **Upload files**. Choose one or more files and select a storage mode:

- **Automatic** sends binary files and files larger than 10 MiB to Git LFS.
- **Ordinary Git** forces the selected files into Git.
- **Git LFS** forces the selected files into S3-backed LFS storage.

Uploads are staged in a private browser draft. The published branch does not change until you enter a commit message and select **Commit changes**. You may discard the draft instead. File deletions use the same draft-and-commit flow.

Large LFS content travels directly between the browser and S3-compatible storage, including multipart uploads. Django authorizes the operation but does not proxy the file body.

## Browse a dataset

Dataset pages provide separate views for:

- **Files**, including directory navigation, README rendering, and individual downloads;
- **History**, containing commits reachable from the selected revision;
- **Branches** and **Tags**;
- **Access**, for users allowed to manage explicit grants; and
- **Settings**, for editing the name, slug, or description and, when authorized, deleting the dataset.

The clone popover provides the Niyān command and repository URL for opening the same dataset locally.

## When to use the CLI instead

Browser commits are useful for manual uploads and small sets of changes. Prefer the CLI when preserving a directory tree, working across branches, reviewing local changes, automating a repeatable process, or maintaining a dataset over time.
