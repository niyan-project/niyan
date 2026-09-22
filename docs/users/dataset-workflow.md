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

`niyan add` follows the repository's standard `.gitattributes` rules and delegates staging to Git. Configure Git LFS for the formats or directories used by your dataset before adding a large tree—for example, `git lfs track "*.jpg"` or `niyan add --lfs images/`. Use `--lfs` or `--git` when you need Niyān to persist an explicit rule for selected paths. Those choices are recorded in `.gitattributes`, so ordinary Git and Git LFS understand the repository too.

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
