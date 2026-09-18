# Niyān Web

The Niyān dashboard is a Nuxt 4 client-rendered SPA built with Nuxt UI and Tailwind CSS. Django owns authentication, authorization, API behavior, Git transport, and production route fallback.

## Development

Run Django on `http://127.0.0.1:8000`, then:

```shell
pnpm install
pnpm dev
```

The Nuxt development server listens on `http://localhost:3000` and proxies `/api`, `/admin`, and `/git` to Django. Override the target with `NIYAN_DJANGO_URL` when needed.

Set `NIYAN_PUBLIC_URL` while building a deployment to its canonical browser-facing origin, such as `https://niyan.example.org`. Niyān uses it in CLI setup instructions and Git URLs. When omitted, the SPA detects the current browser origin.

### URL layout limitation

Niyān v1 must be deployed at the root of its own origin. `https://niyan.example.org/` is supported; `https://example.org/niyan/` is not. `NIYAN_PUBLIC_URL` therefore accepts an origin only and rejects values containing a path, query, fragment, or credentials.

A reverse proxy may terminate TLS or forward the installation to Django, but it must preserve the root-level browser routes and the `/api/`, `/git/`, `/admin/`, and `/static/niyan/` paths. A future path-prefix feature would require coordinated changes to Nuxt's application base and asset URLs, Django's API, Git, static, and SPA fallback routes, and the CLI host model. Rewriting only the generated HTML or one proxy route is not sufficient.

Vue is pinned directly in `package.json` so Nuxt and Nuxt UI resolve to one runtime instance. Keep that pin aligned when upgrading either framework; multiple Vue runtimes break component and slot rendering.

Create the initial account with Django's `createsuperuser` command. There is intentionally no public registration flow.

## Verification

```shell
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

`pnpm build` generates the static application in `.output/public`. Nuxt build assets, fonts, and the application icon use `/static/niyan/`; Django and WhiteNoise serve those files when `NIYAN_WEB_DIST_ROOT` points at the generated public directory.

For a production-style local run, build the web application and then run `uv run python manage.py collectstatic --noinput` from `apps/server` before starting Django. The generated SPA entry document is read directly from `NIYAN_WEB_DIST_ROOT`, while collected immutable assets are served through Django's static-file boundary.
