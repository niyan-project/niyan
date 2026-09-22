---
title: Documentation
description: Learn how to deploy, administer, and use Niyān.
---

# Documentation

Niyān is an open-source, self-hosted data forge for researchers and machine-learning teams. Choose the guide that matches what you need to do.

<div class="niyan-audience-grid">
  <a class="niyan-audience-card" href="/users/">
    <span class="niyan-audience-label">For researchers and teams</span>
    <strong>Using Niyān</strong>
    <span>Sign in, find datasets, work with the CLI, and stream files from Python.</span>
  </a>
  <a class="niyan-audience-card" href="/operators/">
    <span class="niyan-audience-label">For IT and administrators</span>
    <strong>Running Niyān</strong>
    <span>Deploy, configure, administer, maintain, back up, and upgrade a server.</span>
  </a>
</div>

Niyān is currently pre-v1. These guides describe the current `0.x` release line; commands and configuration may still evolve before version 1.0.

## Ways to use Niyān

### Command-line interface

Install the Python package in an isolated environment with `pipx install niyan`. The CLI is the recommended interface for authentication and everyday dataset work.

### Standard Git and Git LFS

Niyān datasets remain ordinary Git repositories. Standard Git and Git LFS are supported, although the Niyān CLI provides more convenient defaults and better support for very large transfers.

### Python and fsspec

Install the same package with `pip install niyan` to expose dataset files as seekable Python streams. Its read-only `fsspec` integration can issue byte-range reads against remote files or download complete files when needed.

### Web application

Create datasets, upload and commit files, browse history, manage groups and access, issue access tokens, and administer a Niyān installation through the web interface.

## Developing Niyān

Architecture decisions, specifications, and maintainer notes live in the [project Wiki](https://github.com/niyan-project/niyan/wiki). They are separate from the product documentation published here.
