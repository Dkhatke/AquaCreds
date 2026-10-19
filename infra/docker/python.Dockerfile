# syntax=docker/dockerfile:1.7
# Multi-stage, multi-arch (linux/amd64 + linux/arm64) image for the Python services.
#   Production targets: `api`, `worker`   (non-editable install, non-root, no dev tools)
#   Development target: `dev`             (editable install of the whole workspace + dev group;
#                                          source is bind-mounted by compose for hot reload)
ARG PYTHON_VERSION=3.12

FROM ghcr.io/astral-sh/uv:0.13.1 AS uv

# ---------------------------------------------------------------- base
FROM python:${PYTHON_VERSION}-slim AS base
COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1
WORKDIR /src
# Dependency manifests first so the dependency layers cache across source edits.
COPY pyproject.toml uv.lock README.md ./
COPY packages/aqua_core/pyproject.toml packages/aqua_core/pyproject.toml
COPY services/api/pyproject.toml services/api/pyproject.toml
COPY services/worker/pyproject.toml services/worker/pyproject.toml

# ---------------------------------------------------------------- production builds
FROM base AS build-api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package aqua-api
COPY packages/aqua_core packages/aqua_core
COPY services/api services/api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package aqua-api

FROM base AS build-worker
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package aqua-worker
COPY packages/aqua_core packages/aqua_core
COPY services/worker services/worker
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package aqua-worker

# ---------------------------------------------------------------- runtime base
FROM python:${PYTHON_VERSION}-slim AS runtime
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --create-home app
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
USER app

# ---------------------------------------------------------------- api
FROM runtime AS api
COPY --from=build-api --chown=app:app /opt/venv /opt/venv
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"]
CMD ["uvicorn", "aqua_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]

# ---------------------------------------------------------------- worker
FROM runtime AS worker
COPY --from=build-worker --chown=app:app /opt/venv /opt/venv
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD celery -A aqua_worker.celery_app inspect ping -d "celery@$HOSTNAME" --timeout 5 || exit 1
CMD ["celery", "-A", "aqua_worker.celery_app", "worker", "--loglevel", "INFO", "-Q", "default,ml,reports", "--concurrency", "2"]

# ---------------------------------------------------------------- dev (local only)
FROM base AS dev
COPY packages packages
COPY services services
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --all-packages
ENV PATH=/opt/venv/bin:$PATH
