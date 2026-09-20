# syntax=docker/dockerfile:1.7

FROM node:24-bookworm-slim AS web-builder
WORKDIR /build/apps/web
RUN corepack enable
COPY apps/web/package.json apps/web/pnpm-lock.yaml apps/web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY apps/web/ ./
RUN pnpm exec nuxt prepare && pnpm build

FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/opt/niyan/server/.venv/bin:$PATH \
    NIYAN_WEB_DIST_ROOT=/opt/niyan/web \
    NIYAN_GIT_ROOT=/var/lib/niyan/repositories

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates git git-lfs \
    && git lfs install --system \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /uvx /usr/local/bin/
WORKDIR /opt/niyan/server
COPY apps/server/pyproject.toml apps/server/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY apps/server/ ./
COPY --from=web-builder /build/apps/web/.output/public/ /opt/niyan/web/

RUN mkdir -p /var/lib/niyan/repositories /opt/niyan/server/staticfiles \
    && NIYAN_SECRET_KEY=container-build-only-not-used-at-runtime NIYAN_DEBUG=false NIYAN_DATABASE_URL=postgresql://niyan:unused@database.invalid:5432/niyan NIYAN_S3_BUCKET=container-build uv run python manage.py collectstatic --noinput \
    && addgroup --system --gid 10001 niyan \
    && adduser --system --uid 10001 --ingroup niyan --home /var/lib/niyan niyan \
    && chown -R niyan:niyan /var/lib/niyan /opt/niyan/server/staticfiles

USER 10001:10001
EXPOSE 8000

CMD ["gunicorn", "project.wsgi:application", "--bind=0.0.0.0:8000", "--access-logfile=-", "--error-logfile=-", "--workers=2", "--timeout=120"]
