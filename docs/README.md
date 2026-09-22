# Niyān website and documentation

This directory contains the public `niyan.org` landing page and product documentation for users and operators. Developer specifications and architecture decisions live in the [repository Wiki](https://github.com/niyan-project/niyan/wiki).

Install dependencies and start the local development server:

```shell
pnpm install
pnpm dev
```

Create the production static site:

```shell
pnpm build
```

The generated site is written to `.vitepress/dist`.
