# `@aquacreds/web`

Next.js 16 (App Router, TypeScript strict) + Tailwind 4. Public website and the AquaCreds console.
Design contract: [`docs/design/DESIGN_DIRECTION.md`](../../docs/design/DESIGN_DIRECTION.md).

```bash
pnpm install                                   # from the repo root
AQUA_API_URL=http://localhost:8000 pnpm --filter @aquacreds/web dev
pnpm --filter @aquacreds/web test              # Vitest + Testing Library
pnpm --filter @aquacreds/web build && pnpm --filter @aquacreds/web e2e   # Playwright + axe
```

## Configuration (read at request time, so one image works everywhere)

| Variable | Meaning |
|---|---|
| `AQUA_API_URL` | API origin as seen from the Next.js server (e.g. `http://api:8000` in compose). Falls back to `NEXT_PUBLIC_API_URL`. Without either, the landing page hides live counts and the console reports that the API is not configured. |
| `FEATURE_DEMO=1` | Show a working Explore Demo (needs `POST /api/v1/demo/session` + `GET /api/v1/demo/status`). Off: the demo page says it is unavailable. |
| `FEATURE_TILES=1` | Request classification tiles (`GET /api/v1/projects/{id}/tilejson?layer=classification&cid=…`). |
| `COOKIE_INSECURE=1` | Allow session cookies over plain http in a production build (local smoke tests only). |

## Auth: backend-for-frontend, httpOnly cookies

Tokens never reach browser JavaScript or `localStorage`:

- `POST /bff/auth/login` calls `auth.login()` from `@aquacreds/api-client`. A `TokenPair` is stored as
  httpOnly cookies `aq_at` (access, expires a little before the JWT) and `aq_rt` (refresh, 30 days).
  An `MfaChallenge` is stored as the httpOnly cookie `aq_mfa`; the browser only learns "MFA required".
- `POST /bff/auth/mfa` exchanges `aq_mfa` + the TOTP / recovery code via `auth.verifyMfa()`.
- `POST /bff/auth/logout` revokes the refresh family on the API and clears the cookies.
- `POST /bff/auth/demo` starts a demo session (access token only, plus `aq_demo` with the banner text).
- `/bff/api/v1/...` proxies every other API call, adding `Authorization: Bearer` from the cookie. On a
  401 it refreshes **once** (single-flight per process, and a rotated pair is remembered for 30 s so
  parallel requests never replay a rotated refresh token, which would revoke the family) and retries.
  Token-bearing endpoints (`auth/login|refresh|register|logout|mfa/verify`, `demo/*`) are not proxied.
- CSRF: cookies are `SameSite=Lax`, `Secure` in production, and every state-changing BFF request
  must carry `x-aquacreds-csrf: 1` (cross-site forms cannot set it) with a same-origin `Origin`.
- The BFF forwards `X-Forwarded-For`; add the web container's network to the API's
  `TRUSTED_PROXY_IPS` so login rate limits apply per user rather than per web server.
- The single-flight refresh lives in process memory: run one web replica (or move it to Valkey
  before scaling out).

Client components use `browserClient()` (the typed client pointed at `/bff`, holding no tokens).

## Registry, blockchain and marketplace

- Console: `/console/registry` (list, `/new`, `/[id]`), `/console/chain`, `/console/marketplace`
  (listings, trades and prices, forward funding). Client components call the API through the BFF.
- Public, no login, light surfaces: `/registry` (projects, `/projects/[id]`, `/vintages`,
  `/retirements`, `/retirements/[id]`, `/verify`) and `/marketplace`. Registry pages are server
  rendered from `AQUA_API_URL`; if it is unset or unreachable they say so instead of showing data.
- Buying, listing and retiring need the user's own wallet: shown as "Connect wallet (coming soon)".
- Playwright has a second `mocked` project: `e2e/mock-api.mjs` serves fixtures and a second Next
  server points at it (`E2E_MOCKED_PORT`, `MOCK_API_PORT`). `SCREENSHOT_DIR=… pnpm e2e` saves screenshots.

## Imagery

`public/imagery/*.webp` are Sentinel-2 L2A composites of Bhitarkanika rendered by
`scripts/render_web_imagery.py` (repo root) with `aqua_geo`; scenes, dates, bbox and processing are in
`public/imagery/provenance.json`. The attribution "Contains modified Copernicus Sentinel data 2024" is
shown wherever the imagery appears.

## Model metrics

The API does not expose classifier validation metrics yet, so held-out metrics are copied from
`ml/model_cards/<version>_metrics.json` (v1 and v1.1, the production model) by `pnpm sync:model-card`;
a unit test fails if a copy drifts. They are shown only for a classification whose `model_version`
matches a card.

## Maps

MapLibre GL 6 with the free OpenFreeMap `dark` style (no key). MapLibre locates its worker next to its
own module, which breaks once bundled, so `scripts/copy-maplibre-worker.mjs` copies it to
`public/vendor/` before `dev`/`build` and the map calls `setWorkerUrl()`.

## Deployment (proposal)

`apps/web/Dockerfile` builds a standalone Node server (≈330 MB, multi-arch, runs as `node`). On the
Oracle VM: add a `web` service to `infra/compose/compose.prod.yaml` with `AQUA_API_URL=http://api:8000`,
and a second Caddy site block (`{$WEB_DOMAIN}` → `reverse_proxy web:3000`) with a page-appropriate CSP
(the API block's `default-src 'none'` would break the site).
