import { defineConfig } from "vitepress";

export default defineConfig({
  title: "Niyān",
  description: "Open-source, self-hosted data version control for research and machine-learning teams.",
  lang: "en-US",
  lastUpdated: true,
  sitemap: {
    hostname: "https://niyan.org",
  },
  head: [
    ["link", { rel: "icon", type: "image/svg+xml", href: "/favicon.svg?v=2" }],
    ["meta", { name: "theme-color", content: "#4f46e5" }],
    ["meta", { property: "og:type", content: "website" }],
    ["meta", { property: "og:site_name", content: "Niyān" }],
    ["meta", { property: "og:title", content: "Niyān — Git-native data version control" }],
    [
      "meta",
      {
        property: "og:description",
        content: "A self-hosted data forge built on Git, Git LFS, and S3-compatible storage.",
      },
    ],
    ["meta", { property: "og:url", content: "https://niyan.org" }],
  ],
  themeConfig: {
    logo: "/favicon.svg?v=2",
    siteTitle: "Niyān",
    nav: [
      { text: "Documentation", link: "/docs/" },
      { text: "Using Niyān", link: "/users/" },
      { text: "Running Niyān", link: "/operators/" },
      { text: "GitHub", link: "https://github.com/niyan-project/niyan" },
    ],
    search: {
      provider: "local",
    },
    outline: {
      level: [2, 3],
    },
    editLink: {
      pattern: "https://github.com/niyan-project/niyan/edit/main/docs/:path",
      text: "Edit this page on GitHub",
    },
    socialLinks: [
      { icon: "github", link: "https://github.com/niyan-project/niyan" },
    ],
    footer: {
      message: "Released under the Apache License 2.0.",
      copyright: "Niyān contributors",
    },
    sidebar: {
      "/docs/": [
        {
          text: "Documentation",
          items: [{ text: "Overview", link: "/docs/" }],
        },
      ],
      "/users/": [
        {
          text: "Using Niyān",
          items: [{ text: "Overview", link: "/users/" }],
        },
        {
          text: "The niyan CLI",
          collapsed: false,
          items: [
            { text: "CLI overview", link: "/users/cli/" },
            { text: "Get started", link: "/users/getting-started" },
            { text: "Work with datasets", link: "/users/dataset-workflow" },
            { text: "Browse and download", link: "/users/browse-and-download" },
            { text: "Python and fsspec", link: "/users/python-and-fsspec" },
            { text: "Standard Git and Git LFS", link: "/users/standard-git" },
          ],
        },
        {
          text: "The user dashboard",
          collapsed: false,
          items: [
            { text: "Dashboard overview", link: "/users/dashboard/" },
            { text: "Datasets and browser commits", link: "/users/dashboard/datasets" },
            { text: "Groups and access", link: "/users/dashboard/groups-and-access" },
            { text: "Account security and tokens", link: "/users/dashboard/account-security" },
          ],
        },
      ],
      "/operators/": [
        {
          text: "Running Niyān",
          items: [
            { text: "Overview", link: "/operators/" },
            { text: "Deploy with Docker Compose", link: "/operators/deployment" },
            { text: "Configure Niyān", link: "/operators/configuration" },
            { text: "Administer the system", link: "/operators/administration" },
            { text: "Routine maintenance", link: "/operators/maintenance" },
            { text: "Back up and restore", link: "/operators/backup-and-restore" },
            { text: "Upgrade", link: "/operators/upgrades" },
          ],
        },
      ],
    },
  },
});
