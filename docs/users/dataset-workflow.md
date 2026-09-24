---
title: Work with datasets
description: Create, clone, and change versioned datasets with the Niyān CLI.
---

# Work with datasets

## Clone an existing dataset

```shell
niyan dataset clone genomics/reference-data
cd reference-data
```

Run `niyan status` to see the current branch and working-tree changes.

## Add and commit files

```shell
niyan add samples/
niyan commit -m "Add sequencing run 42"
niyan push
```

The first interactive `niyan add` in a repository without a root `.gitattributes` file asks which discovered file formats should use S3-backed Git LFS storage. Git supplies the recursive file list and applies `.gitignore`; Niyān groups the remaining names by extension without opening or classifying every file. Compound compressed formats such as `.nii.gz` and `.tar.zst` appear as distinct choices. Your selections become ordinary repository-wide `.gitattributes` rules understood by standard Git LFS.

Once `.gitattributes` exists, Niyān treats it as the repository's complete storage policy and skips discovery. Non-interactive calls also skip the selector. Use `--lfs` or `--git` when you deliberately want to persist an explicit rule for selected paths. Interactive staging displays a status indicator by default; add `--verbose` to replace it with Git's per-file output.

Common commands mirror familiar Git operations:

```shell
niyan diff
niyan log
niyan branch list
niyan switch experiment
niyan pull
niyan push
```

Use `niyan --help` or `niyan <command> --help` for the complete command options.

## Create a dataset

You can instead create the empty dataset from a terminal:

```shell
niyan dataset create genomics/new-dataset --name "Processed reference data"
```

The group path must already exist and you must have permission to create datasets in it. Clone the new dataset, add its first files, commit, and push as usual.

You can also [create and populate a dataset from the user dashboard](/users/dashboard/datasets) when a manual browser workflow is more convenient.

## Use a dataset inside another project

Niyān can attach a dataset as a Git submodule in a research or ML project:

```shell
niyan dataset add genomics/reference-data data/reference
```

The parent project records the exact dataset commit, making an experiment reproducible without placing the dataset contents in the parent repository. Other contributors can materialize recorded dependencies with:

```shell
niyan dataset update
```

Remove a dependency with `niyan dataset remove <path>`.

## Branches, tags, and local storage

Branches and tags behave like their Git equivalents. The CLI's shallow defaults reduce initial storage, but Git must retain local objects that are not available remotely—for example, unpushed work on a branch you leave. Push important work before pruning local data.

Inspect cache use with `niyan cache status` and remove safely recoverable cached objects with `niyan cache prune`.
