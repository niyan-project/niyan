---
title: Standard Git and Git LFS
description: Use a Niyān dataset with ordinary Git and Git LFS commands.
---

# Standard Git and Git LFS

Niyān datasets are ordinary Git repositories, and standard Git over HTTPS plus Git LFS is an officially supported workflow. The Niyān CLI remains recommended because it configures authentication, large-file transfers, and storage-conscious defaults automatically.

## Clone with Git

Copy the HTTPS clone URL from the dataset page, then authenticate with a Niyān access token when Git asks for a password:

```shell
git clone --depth=1 https://data.example.edu/git/<dataset-id>.git
```

`--depth=1` is optional but strongly recommended for large datasets. An ordinary `git clone` cannot be made shallow by the server, so this choice belongs to the user.

## Track large files

Repositories created through Niyān use normal `.gitattributes` rules understood by Git LFS:

```shell
git lfs track "*.parquet"
git add .gitattributes data/example.parquet
git commit -m "Add example data"
git push
```

Niyān does not reject a push merely because a file could have been stored more efficiently in LFS. When using Git directly, you are responsible for choosing suitable tracking rules.

## Expected differences

- Ordinary Git does not apply Niyān's shallow and single-branch defaults unless you request them.
- Basic Git LFS transfers work, but very large objects may require the Niyān transfer adapter configured by the CLI.
- Niyān conveniences such as remote tree browsing, filtered materialization, browser commits, and fsspec access remain available alongside a Git checkout.
- Authorization is the same: Git and Git LFS requests are checked by the Niyān server using your access token.

You can freely mix Niyān and Git commands in the same repository because Niyān builds on their standard repository formats rather than replacing them.
