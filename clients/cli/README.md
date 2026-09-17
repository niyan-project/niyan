# Niyān CLI

The standalone `niyan` command is the supported interface for authenticating to a Niyān installation and coordinating dataset repository workflows. It consumes the public REST and Git-over-HTTPS interfaces and never imports server implementation code.

The initial CLI requires Git on `PATH`; Niyān invokes it internally rather than asking users to manage dataset repositories with Git commands. Git LFS transfer support is intentionally deferred to its protocol slice.

The initial development commands are:

```shell
uv sync
uv run python -m unittest discover -s tests
uv run niyan --help
```

Access-token secrets use the operating-system credential store through `keyring`. Headless environments without a compatible backend may opt into the explicitly insecure mode-`0600` file store with `--insecure-storage`.
