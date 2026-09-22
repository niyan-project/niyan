---
title: Browse and download
description: Inspect and retrieve dataset content without cloning every file.
---

# Browse and download

Large datasets do not always belong on your workstation. Niyān can inspect remote trees and retrieve only the content you need.

## Browse remotely

```shell
niyan dataset tree genomics/reference-data
niyan dataset tree genomics/reference-data path/to/directory
```

Stream one file to standard output:

```shell
niyan dataset cat genomics/reference-data path/to/file.csv
```

These commands respect the revision selected by the command and your dataset permissions.

## Download files

Download an individual file or directory without creating a Git checkout:

```shell
niyan dataset download genomics/reference-data path/to/file.csv
```

For a working copy that contains only part of a dataset, use include and exclude filters when cloning or pulling:

```shell
niyan dataset clone genomics/reference-data --include "samples/2026/**"
niyan pull --exclude "raw/**"
```

Use `--metadata-only` to obtain the repository structure and LFS pointers without downloading LFS objects. You can fetch the needed content later.

## Choose the right method

- Clone a dataset when you intend to edit, commit, branch, or tag it.
- Download directly when you need a file once and do not need Git history.
- Use Python and fsspec when an analysis should stream bytes or perform ranged reads without materializing a complete file first.
