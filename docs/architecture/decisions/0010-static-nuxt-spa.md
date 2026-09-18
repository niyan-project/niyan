# ADR 0010: Deploy the Nuxt Web Application as a Static SPA

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

Niyān's web interface is an authenticated data forge. Its namespace, dataset, repository, credential, and access-management views depend on installation-local data and the current Django session. The initial product has no public repositories, social discovery, or other indexable pages that justify a second server-rendering runtime.

Running Nuxt server-side rendering in production would add a permanent Node.js service and require session, CSRF, caching, and failure behavior across two application servers. Prerendering application routes would not help because their content is user-specific and unknown at build time.

Development still needs fast Nuxt hot-module replacement while exercising Django's same-origin session and API behavior. Production should remain straightforward to self-host and should not require users to understand a split-origin browser deployment.

## Decision

Build the Nuxt 4 application with server-side rendering disabled and deploy its generated client application as static files under the same origin as Django.

Nuxt's generated build assets, bundled fonts, and application icon use `/static/niyan/` rather than framework-specific root paths. Django serves the SPA entry document for web routes after the API, Git, admin, and static routes have had an opportunity to match. WhiteNoise serves immutable generated assets in the initial production topology. A reverse proxy may serve the same files later without changing browser URLs or application code.

During development, the Nuxt development server is browser-facing and Nitro's development proxy forwards Django-owned paths. Browser code uses relative URLs, includes session credentials, and supplies Django's CSRF token on unsafe requests. Django remains the only authorization authority.

The initial design uses Nuxt UI and Tailwind CSS rather than a parallel component system. Indigo is the semantic primary color. Neutral surfaces use Zinc-like black, gray, and white values so dark backgrounds are not tinted by the accent. Light, dark, and operating-system-following appearances are supported.

## Consequences

- Production needs no Node.js runtime after the frontend build completes.
- Authenticated deep links require a Django SPA fallback but do not require per-route server rendering.
- Generated output contains one entry document and cacheable hashed assets rather than one monolithic HTML file.
- The browser and Django share an origin, avoiding a public CORS contract and keeping cookie and CSRF behavior consistent.
- V1 deployments require a dedicated origin root. Mounting beneath a prefix such as `/niyan/` is unsupported because SPA navigation, generated assets, Django API and Git routes, and CLI host normalization all currently assume origin-root paths.
- Public server-rendered pages may be introduced later as a separately reviewed hybrid deployment decision if a concrete indexing requirement appears.
- Frontend code must not depend on Nuxt server routes or server-only rendering behavior in v1.
