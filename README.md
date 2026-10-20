# AquaCreds v2

**Blockchain-based Blue Carbon Registry & MRV System** for mangroves and coastal wetlands.
This is a production-grade rebuild of the team VISSDOM Smart India Hackathon 2025 project (SIH25038).

> Status: **planning / Phase 0**. Nothing here issues real carbon credits. All figures are estimates pending
> accredited verification, and tokens live on the Polygon Amoy **testnet**.

## What it does
1. Coastal NGOs and panchayats register restoration projects with real boundaries.
2. Field teams capture geotagged evidence in an offline-first mobile app.
3. A satellite + ML pipeline (Sentinel-1/2, LightGBM, foundation-model embeddings) maps the ecosystem and
   estimates biomass **with uncertainty**.
4. A documented carbon engine computes net removals per methodology (India CCTS BM FR05.001 / Verra
   VM0033).
5. Verifiers review evidence on a web dashboard; approved reports are hashed and anchored on Polygon.
6. Credits are issued as ERC-1155 vintages, then traded, retired, and publicly traceable.

## Quick start (local development)
Prerequisites: Docker Desktop, [uv](https://docs.astral.sh/uv/), make. Full list: [requirements/](requirements/README.md).

```bash
cp .env.example .env          # local dev defaults
make sync                     # create .venv (Python 3.12) with all packages + dev + ML tools
make dev                      # build & start: PostGIS, Valkey, S3 (SeaweedFS), migrate, API, worker
make smoke                    # readiness check + API → queue → worker round trip
make test lint                # tests, ruff, mypy
```

| Service | URL |
|---|---|
| API docs (Swagger) | http://localhost:8000/docs |
| Liveness / readiness | http://localhost:8000/healthz · http://localhost:8000/readyz |
| PostGIS | `localhost:5432` (aquacreds / aquacreds) |
| Valkey | `localhost:6379` |
| S3 (SeaweedFS) | http://localhost:8333 (aquadev / aquadev-secret) |

Workflow: never commit to `main`; use a feature branch and open a PR. `pre-commit install` enables the
lint/format/secret-scan hooks.

## TypeScript workspace and API client
Prerequisites: Node 24+ and pnpm 12 (`npm i -g pnpm@12.14.0`, or `npx pnpm@12.14.0 …`; Node 25+ no
longer bundles corepack). pnpm workspaces + Turborepo; Biome for lint/format.

```bash
pnpm install                  # JS workspace (apps/*, packages/api-client, tools/*)
pnpm gen:api                  # FastAPI → packages/api-client/openapi.json → src/schema.d.ts
pnpm check                    # turbo: lint + typecheck + test
```

`@aquacreds/api-client` (no React dependency; used by web and mobile) wraps `openapi-fetch` with bearer
injection, single-flight refresh on 401 (rotating refresh tokens), logout on refresh failure, typed
helpers for the main flows and a presigned-upload helper (SHA-256 via SubtleCrypto). Any API change
must be followed by `pnpm gen:api` and committing the result: the `Frontend` CI workflow fails on drift.

## Production deployment
Free hosting on an Oracle Cloud Always Free ARM VM (Docker Compose + Caddy, Cloudflare R2, nightly
backups, tag-triggered deploys): see the runbook [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## API (current)
Swagger UI at http://localhost:8000/docs. Implemented so far:
- **Auth:** `POST /api/v1/auth/{register,login,refresh,logout}`, `GET /api/v1/auth/me`
- **Organisations:** `POST/GET /api/v1/orgs`, `GET /api/v1/orgs/{id}`, `GET/POST /api/v1/orgs/{id}/members`
- **Projects:** `POST/GET /api/v1/projects`, `GET/PATCH /api/v1/projects/{id}`,
  `POST /api/v1/projects/{id}/documents/upload-url`, `POST /api/v1/projects/{id}/documents`,
  `POST /api/v1/projects/{id}/submit`
- **Audit (registry admin):** `GET /api/v1/audit`, `GET /api/v1/audit/verify`

Create the first registry admin: `docker compose exec api aqua-api create-user --email you@example.org`

## Repository layout
```
packages/aqua_core   shared settings, logging, DB + storage helpers
services/api         FastAPI app (+ Alembic migrations, `aqua-api` CLI)
services/worker      Celery workers (queues: default, geo, ml, chain, reports)
packages/api-client  TypeScript API client generated from the OpenAPI schema (pnpm)
tools/openapi-codegen  openapi-typescript runner (needs TypeScript 5 compiler API)
scripts/             export_openapi.py (deterministic OpenAPI export)
infra/docker         multi-stage, multi-arch Dockerfile (targets: api, worker, dev)
compose.yaml         local dev stack
docs/                plan, stack, datasets, research notes, design prompts, context
```

## Documentation
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md): phases, architecture, science, deployment
- [Tech stack evaluation](docs/TECH_STACK.md): choices and alternatives
- [Dataset catalogue](docs/DATASETS.md): data sources, licences, usage
- [Project context](docs/CONTEXT.md): decisions and status log
- [Requirements & prerequisites](requirements/README.md)

## Licence
To be decided (MIT proposed for code; datasets keep their own licences, see docs/DATASETS.md).
