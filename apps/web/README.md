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
- The `mocked` Playwright project drives these pages against `e2e/mock-api.mjs` (`E2E_MOCKED_PORT`,
  `E2E_REGISTRY_API_PORT`). `SCREENSHOT_DIR=… pnpm e2e` saves screenshots.

## Internationalisation (English, Hindi, Odia)

`next-intl` 4.14.9 (pinned) **without locale URL prefixes**: the language is the `aq_locale` cookie
(set by the switcher, one year, `SameSite=Lax`), read in `src/i18n/request.ts`. Why not the built-in
`[lang]` segment pattern or next-intl routing: every existing URL, the console auth proxy matcher,
demo links and the OpenAPI-driven `link` paths stay unchanged, and there is no duplicate-URL SEO
question for an app behind a login. The cost is that pages are rendered per request (they already
were: `force-dynamic`).

- `messages/en.json` is the source. `hi.json` and `or.json` are laid over it (`mergeMessages`), so any
  untranslated key renders in **English**, never as a key. Unknown cookie values fall back to English.
- `hi.json` / `or.json` are **AI-drafted and not reviewed by a native speaker**; each carries
  `_meta.reviewed: false` (asserted by a unit test) and the UI says so in English plus the language.
  Flip `reviewed` only after a native-speaker review.
- Carbon / legal honesty labels use `<HonestyNote>`: the English text under `honesty.*` is always
  shown (`lang="en"`), and a translation (`honestyLocal.*`, hi/or only) is added beneath it, never in
  place of it. hi/or must not define `honesty.*` (tested).
- Server components use `getTranslations`, client components `useTranslations`. Hindi and Odia glyphs
  come from self-hosted Noto Sans Devanagari / Oriya via `next/font`.
- Strings still English-only: monitoring panels, marketplace, how-it-works, login/demo, most landing
  sections, status pill labels, and API-written text (notification titles, report notes).

## End-to-end tests

`pnpm e2e` starts five processes (ports and roles are listed at the top of `playwright.config.ts`):
the app with no API (smoke); `e2e/fixtures/mock-api.mjs` plus a second app instance pointed at it with
`FEATURE_DEMO=1` (console tests); and `e2e/mock-api.mjs` plus a third app instance (the `mocked` project:
registry, chain and marketplace pages). The mock API is a **test fixture** with obviously
synthetic records ("Fixture ..."); it exists so the console can be driven without Postgres and must
never be used for real or promotional screenshots. `SCREENSHOT_DIR=... pnpm e2e` writes screenshots.

## Imagery

`public/imagery/*.webp` are Sentinel-2 L2A composites of Bhitarkanika rendered by
`scripts/render_web_imagery.py` (repo root) with `aqua_geo`; scenes, dates, bbox and processing are in
`public/imagery/provenance.json`. The attribution "Contains modified Copernicus Sentinel data 2024" is
shown wherever the imagery appears.

## Model metrics

Held-out metrics are copied from `ml/model_cards/<version>_metrics.json` (v1 and v1.1, the
production model) by `pnpm sync:model-card`; a unit test fails if a copy drifts. They are shown only
for a classification whose `model_version` matches a card.

The API now serves the same numbers (checked against the same JSON files in the API tests):
`ClassificationOut.model_card` on every classification (null for a version the API has no
manifest for) and `GET /api/v1/models`, `/models/{family}`, `/models/{family}/{version}` (public,
cacheable; families `ecosystem-classifier` and `photo-verifier`). Switching the web app to them and
dropping the copied JSON is a follow-up.

Other endpoints added for the web app: `GET /api/v1/public/stats` (landing-page counts in one
cacheable call: public projects, area, by status, credits issued/retired/replaced from indexed
chain events, indexer freshness) and `GET /api/v1/projects/{id}/evidence-points` (GeoJSON for the
monitoring map; snapped to the plot centre or a 0.005° grid cell unless `precision=raw`, which only
the project's field team and staff may request). Typed helpers: `public.stats()`, `models.*`,
`field.evidencePoints()`.

## Maps

MapLibre GL 6 with the free OpenFreeMap `dark` style (no key). MapLibre locates its worker next to its
own module, which breaks once bundled, so `scripts/copy-maplibre-worker.mjs` copies it to
`public/vendor/` before `dev`/`build` and the map calls `setWorkerUrl()`.

## Deployment

`apps/web/Dockerfile` builds a standalone Node server (about 330 MB, multi-arch, runs as `node`). CI
publishes `ghcr.io/dkhatke/aquacreds-web`; `infra/compose/compose.prod.yaml` runs it (single replica,
512 MB, read-only root fs, `AQUA_API_URL=http://api:8000`) behind a Caddy `WEB_DOMAIN` block with the
CSP. Behind the TLS proxy the BFF's CSRF check compares `Origin` with the forwarded public origin.
Runbook: [`docs/DEPLOYMENT.md`](../../docs/DEPLOYMENT.md).
