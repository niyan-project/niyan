---
title: Python and fsspec
description: Stream and download Niyān dataset files from Python.
---

# Stream datasets from Python with fsspec

The same `niyan` package that provides the CLI also registers a read-only [fsspec](https://filesystem-spec.readthedocs.io/) filesystem. Its central purpose is to let Python code stream remote dataset files without cloning the dataset or downloading each complete file first.

Install it in the Python environment used by your analysis:

```shell
pip install niyan
```

## Stream without a local copy

Niyān URLs include the server hostname, dataset path, file path, and revision:

```python
import fsspec

url = "niyan://data.example.edu/genomics/reference-data/tables/samples.csv?revision=main"

with fsspec.open(url, mode="rb") as remote_file:
    header = remote_file.read(4096)
```

`remote_file` is a normal seekable binary file object. Reads are translated into bounded HTTP range requests, so code can jump to a distant part of a multi-gigabyte file without transferring the bytes before it:

```python
with fsspec.open(url, mode="rb", block_size=8 * 1024 * 1024) as remote_file:
    remote_file.seek(4_000_000_000)
    sample = remote_file.read(1024 * 1024)
```

For Git LFS objects, those ranges are streamed directly from S3-compatible storage using short-lived authorized URLs. Niyān refreshes an expired transfer authorization without changing the immutable object being read. Ordinary Git blobs use the server's ranged repository endpoint.

You can also process a complete file incrementally without storing it on disk:

```python
with fsspec.open(url, mode="rb") as remote_file:
    while chunk := remote_file.read(8 * 1024 * 1024):
        process(chunk)
```

This keeps memory bounded, although reading every chunk still transfers the complete file.

::: tip What “streaming” guarantees
Niyān supplies a seekable, buffered byte stream. Whether a library reads only selected ranges or eventually consumes the entire file depends on that library and the file format. Formats designed for random access can benefit most from range reads.
:::

## Use it with other Python libraries

Libraries that accept fsspec URLs, fsspec filesystems, or seekable binary file objects can read Niyān content without a Niyān-specific integration. Pass the `niyan://` URL directly where a library supports fsspec, or open it with `fsspec.open()` and pass the resulting file object.

## Create a filesystem explicitly

```python
import fsspec

filesystem = fsspec.filesystem(
    "niyan",
    host="data.example.edu",
    dataset="genomics/reference-data",
)

print(filesystem.ls("tables"))
```

The client can reuse credentials created by `niyan auth login`. A token can also be supplied explicitly for a non-interactive environment; keep it outside source control and restrict its lifetime and permissions.

## Download locally

```python
filesystem.get_file("tables/samples.csv", "samples.csv")
filesystem.get("images", "local-images", recursive=True)
```

Downloads are written through a temporary `.niyan-part` file and moved into place after completion, preventing an interrupted transfer from looking like a complete result.

Use streaming when an analysis can consume remote bytes directly. Use `get_file()` or `get()` when repeated access makes a complete durable local copy more practical.

::: warning Read-only interface
The fsspec integration is intentionally read-only. Use the web application's browser commits, the CLI, or standard Git and Git LFS to publish changes so they remain versioned.
:::
