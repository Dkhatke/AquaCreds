# Demo data (`aqua-api seed-demo`)

A **synthetic**, clearly labelled demo project for UI work and SIH demos. It is not field data,
not satellite data and not a real project. Never show it as real.

## Run it

```bash
make sync                     # local .venv with every workspace package
make dev                      # dev stack: PostGIS, Valkey, SeaweedFS, migrate, api, worker
uv run aqua-api seed-demo     # uses .env (DATABASE_URL, S3_*); default seed 42
uv run aqua-api seed-demo --seed 7   # another demo project on a different, non-overlapping site
```

Inside the stack instead: `docker compose exec api aqua-api seed-demo` (the dev image has all
packages; the slim production API image does not, and production is refused anyway).

The command prints the demo logins. Passwords come from the `DEMO_PASSWORD` environment variable
(min. 10 characters; a shell variable, it is not read from `.env`) or
are generated for each new account and printed once. Re-running is safe: existing rows are found
and only missing steps are completed; existing passwords are left unchanged.

### With credits on a local chain (`--with-chain`)

Only against a local Anvil (chain id 31337; anything else is refused):

```bash
docker run --rm -p 8545:8545 ghcr.io/foundry-rs/foundry:v1.7.0 anvil --host 0.0.0.0 --chain-id 31337
REGISTRAR_ADDRESS=0x70997970C51812dc3A010C7d01b50e0d17dc79C8 \
VERIFIER_ADDRESS=0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC \
ISSUER_ADDRESS=0x90F79bf6EB2c4f870365E785982E1f101E93b906 \
./contracts/forge.sh script script/Deploy.s.sol --rpc-url http://host.docker.internal:8545 \
  --broadcast --private-key <Anvil account 0 key>
# .env: the printed addresses (CHAIN_REGISTRY_ADDRESS, CHAIN_CREDIT_ADDRESS,
# CHAIN_MARKETPLACE_ADDRESS, CHAIN_USDC_ADDRESS, CHAIN_FORWARD_FUND_ADDRESS), CHAIN_ID=31337,
# CHAIN_RPC_URL=http://localhost:8545 and RELAYER_MNEMONIC="test test test test test test test
# test test test test junk" (Anvil's public dev mnemonic; never use it anywhere else)
uv run aqua-api seed-demo --with-chain
```

`forge.sh` runs Foundry in Docker when it is not installed locally, so the RPC URL is
`host.docker.internal` from inside that container (`localhost` with a local Foundry); it forwards
the `*_ADDRESS` role variables into the container.

The three addresses are accounts 1–3 of Anvil's public dev mnemonic (the relayer's default
registrar / verifier / issuer indices). `--with-chain` anchors both reports (registers the
project, then `submitReport` + `approveReport` for the monitoring report), issues the monitoring
report's lCCC to the organisation's custodial wallet, and runs the indexer, so the public registry
shows a vintage. It mines blocks with `anvil_mine` to reach the confirmation depth. Stop the Anvil
container when done.

## What is created, and what is synthetic

| Item | Labelling |
|---|---|
| Organisation `Demo Mangrove Collective (synthetic, seed N)` | name |
| 5 users: org admin, field agent, verifier, registry admin, buyer (`demo.<role>.sN@example.org`) | "(synthetic)" names, reserved `example.org` domain |
| Explore Demo visitor `demo.visitor.sN@example.org`: platform role `user`, `member` of the demo org only, random password that is never printed (nobody can sign in as it) | "Demo Visitor (synthetic, read-only)" |
| Project `Demo Bhitarkanika Restoration (synthetic, seed N)`: a 0.019° square (~416 ha) on real Bhitarkanika mangroves, Odisha (see [Boundary](#boundary-real-mangroves-synthetic-project)) | name + description "DEMO - synthetic data …" |
| Tenure documents (forest-department agreement, CRZ clearance, carbon-rights agreement) | generated one-page PDFs reading "SYNTHETIC DEMO DOCUMENT … not a real permit" |
| Baseline (2025-01 to 2026-03) and monitoring (2026-04 to 2026-12) periods, 5 plots (3 dense, 2 sparse) with tree measurements | seeded random values (`random.Random(seed)`) |
| Plot-centre photos (monitoring period) | generated images (coloured blocks + "SYNTHETIC DEMO PHOTO" banner), EXIF `Make: AquaCreds demo generator`, description = the demo tag; run through the normal evidence checks |
| 10 `scene_observations` rows (NDVI statistics) | `provider = synthetic-demo`, item ids `SYNTHETIC-DEMO-…`; never Sentinel-2 data |
| Baseline + monitoring MRV reports, approved by the demo verifier | report notes = the demo tag; the satellite provenance says `synthetic-demo` |

The carbon figures in the reports are computed by the real engine (`aqua_carbon` via `aqua_mrv`)
from these synthetic inputs, so they are traceable to inputs, methodology profile and versions,
but they are **not** real removals. No ecosystem-classification rows are seeded.

Photo capture times are the time of seeding (capture nonces are short-lived), so only the
monitoring period, whose window contains the seeding date, gets photos.

Demo staff accounts have no TOTP; the seed refuses to run when `MFA_REQUIRED_FOR_STAFF` is on.

## Boundary: real mangroves, synthetic project

The boundary geometry is real; the project is not. Each seed uses one of six grid-aligned
0.019° squares (416.3–416.4 ha, geodesic WGS84) in Bhitarkanika mangrove forest
(`DEMO_SITES` in `services/api/src/aqua_api/demo.py`, chosen by `seed % 6`; the default seed 42
uses site 0, `--seed 7` site 1). Earlier versions placed 0.02° squares on a grid from
86.80 E, 20.62 N, which landed on farmland and river: seeds 0–3 and 42 were 0–53 % mangrove by
GMW (the default seed 42 was 0 %).

| Site | South-west corner (lon, lat) | Mangrove (GMW v4 2020) |
|---|---|---|
| 0 (seed 42) | 86.837, 20.702 | 99.66 % |
| 1 | 86.895, 20.734 | 97.60 % |
| 2 | 86.833, 20.724 | 96.86 % |
| 3 | 86.870, 20.734 | 95.24 % |
| 4 | 86.929, 20.691 | 95.98 % |
| 5 | 86.960, 20.681 | 98.02 % |

Method: Global Mangrove Watch v4.0 2020 (`data/raw/gmw_v4`, the DVC download, tile
`GMW_N21E086_v4019_mng.tif`, 0.0001° pixels, 1 = mangrove). A sliding-window search over
86.75–86.98 E, 20.55–20.80 N kept non-overlapping squares above 95 % mangrove; the share is the
fraction of the 190 × 190 pixels inside each square that GMW maps as mangrove (the squares are
grid-aligned, so it is exact). The non-mangrove remainder is narrow creeks and gaps, so every site
clears the >90 % mangrove submission rule without covering open water. The five plots of seeds
0–5, 7 and 42 all sample a GMW mangrove pixel. The squares lie inside the India coastal bounding
box and the 0.5–5,000 ha area limits, and never overlap each other, so different seeds can be
seeded side by side (seeds with the same `seed % 6` collide on the cross-organisation overlap
check).

## Explore Demo (no account, read-only)

The website's **Explore Demo** button opens the console on the seed-demo organisation without
creating an account. It is **off by default** and must be switched on deliberately.

### Enable it (SIH judging)

1. Seed the demo (see above); note the printed `DEMO_ORG_ID=…` line. Re-running `seed-demo` on an
   older demo database adds the visitor account it needs.
2. Set, for the API:
   ```bash
   DEMO_MODE_ENABLED=true
   DEMO_ORG_ID=<the printed organisation id>   # or DEMO_ORG_SLUG=<its slug>
   DEMO_SESSION_TTL_MIN=60                      # session length (default 60)
   RATE_LIMIT_DEMO_SESSION_IP=20/hour           # per-IP limit on new sessions (default)
   ```
   Production (`APP_ENV=production`) refuses to start with demo mode on unless `DEMO_ORG_ID` or
   `DEMO_ORG_SLUG` is set and rate limiting is on, and logs a `demo_mode.enabled_in_production`
   warning at every start.
3. Restart the API and check `GET /api/v1/demo/status` → `{"enabled": true, "banner": …}`.
4. **Turn it off after judging** (`DEMO_MODE_ENABLED=false`, restart): new sessions answer 404 and
   every issued demo token stops working at once.

Note: `seed-demo` itself refuses `APP_ENV=production` (its staff demo accounts have no TOTP), so a
production deployment cannot be seeded in place; demo mode there needs an organisation seeded by
another route (open item, see the PR).

### API

| Endpoint | Answer |
|---|---|
| `GET /api/v1/demo/status` (public, `Cache-Control: public, max-age=60`) | `{enabled, banner}` |
| `POST /api/v1/demo/session` (no body, no auth) | `{access_token, expires_in, demo: true, org_id, project_ids, banner: "Demonstration data — synthetic, testnet"}`; 404 when demo mode is off; 503 `demo_unavailable` when the org or visitor is missing or the org is not labelled synthetic; 429 over the per-IP limit |

Use `access_token` as a Bearer token. There is no refresh token: when it expires, ask for a new
session. The web app shows `banner` on every page of a demo session and labels any chain
transaction it shows as a demonstration (testnet) transaction.

### Security model

- **Only synthetic data.** Sessions are issued only for the configured organisation, and only
  when its name contains `(synthetic` (every seed-demo organisation does). Rename it and existing
  tokens are refused on their next request and no new sessions are issued.
- **A distinct token.** JWT with audience `aquacreds-demo` (ordinary tokens use `aquacreds-api`,
  so the normal decoder can never accept a demo token) and claims `demo: true`, `scope: read`,
  `org`. Short-lived (`DEMO_SESSION_TTL_MIN`), no refresh token, and only honoured while
  `DEMO_MODE_ENABLED` is on.
- **A least-privileged principal.** The token's subject is the demo org's visitor account (plain
  `user`, `member` of the demo org only, re-checked on every request: an extra membership, a role
  change or deactivation ends the session). Every existing access check therefore treats it like
  any outsider: other organisations' projects, reports and transactions answer **404**.
- **Read-only, enforced centrally** (`aqua_api.demo_mode`):
  1. `DemoReadOnlyMiddleware` answers **403 `demo_read_only`** to any request other than
     GET/HEAD/OPTIONS that carries a token claiming to be a demo token, before routing. That
     covers every route, public ones included, so a demo session cannot write, upload (uploads
     are presigned-PUT requests made by POST), review, relay or sign a chain transaction (every
     relayed action is a POST), log out or change settings.
  2. `get_current_user` lets a demo token reach only the GET endpoints on the allow-list
     `DEMO_READABLE_ENDPOINTS` (org-scoped reads: projects, periods, plots, submissions, analysis,
     classifications, MRV reports, stewardship, project chain records, the project's forward-fund
     view, own notifications, `/auth/me`). Everything else, staff and admin endpoints included
     (`/audit`, `/chain/status`, member lists, MFA, the cross-org forward-fund book), answers
     **403 `demo_forbidden`**. New endpoints are denied until someone reviews them and adds them.
  3. Presigned **downloads** of the demo org's artifacts (MRV report JSON/PDF/CSV,
     classification rasters) come from allow-listed GETs, so they work.
- **Tested automatically.** `services/api/tests/test_demo_mode.py` walks every route of the app:
  every non-GET route must refuse a demo token with `demo_read_only`, every authenticated GET
  route that is not allow-listed must answer `demo_forbidden`, and every allow-list entry must be
  an existing GET route. A new route is covered without anyone editing the test.
- **Rate-limited and audited.** `POST /demo/session` is limited per client IP
  (`RATE_LIMIT_DEMO_SESSION_IP`, Valkey, proxy-aware like the auth limits). Each session writes a
  `demo.session_created` entry to the hash-chained audit log (actor = visitor, entity = the demo
  org) with the token id and a keyed HMAC of the IP; the raw IP is never stored.

### Reset the demo data

The demo org is ordinary rows in the database, so a session can never change it (it is read-only),
but to start from a clean dataset:

```bash
# dev stack: delete the data volumes, start again (the stack runs the migrations), seed again
make nuke && make dev
uv run aqua-api seed-demo [--with-chain]
```

Then update `DEMO_ORG_ID` (a fresh seed creates a new organisation id) and restart the API. A
plain re-run of `seed-demo` on the existing database only completes missing steps; it does not
undo anything.

## End-to-end test

`services/api/tests/test_e2e_lifecycle.py` runs the whole lifecycle (register → report → approve →
anchor → issue → list → buy → retire → public registry → stewardship → audit) on PostGIS + Anvil
containers: `uv run pytest -m e2e`.
