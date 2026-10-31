# AquaCreds: the complete project story

Written 2026-10-31. A plain-language guide to what AquaCreds is, what was built, which technologies
were used and why, how the work was done, what went wrong, and what is still open.
For the short, living version of decisions and status see [CONTEXT.md](CONTEXT.md); for the plan see
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

---

## 1. What AquaCreds is

AquaCreds measures how much carbon coastal forests (mangroves first) store, gets an accredited person
to check it, records the proof on a blockchain, and lets buyers purchase the resulting carbon credits.

It began as an entry for the **Smart India Hackathon 2025**, problem **SIH25038** "Blockchain-Based Blue
Carbon Registry and MRV System" (team VISSDOM). This work rebuilt it from scratch as a production-style MVP.

**The problem.** Mangroves, seagrass and salt marshes lock away a lot of carbon. Communities that protect
them can earn money from carbon credits, but only if the carbon is proven real. That proof is called
**MRV**:

- **Monitoring**: watch the forest (field visits, satellites).
- **Reporting**: write the numbers down in a standard way.
- **Verification**: an accredited verifier checks the report.

Done by hand it is slow, paper-based and easy to fake.

**The flow we built**

1. An NGO or panchayat registers a project and draws its boundary on a map.
2. Field teams use a mobile app to take geotagged photos and measure sample plots.
3. Satellites and machine learning map the mangroves and raise alerts if forest is lost.
4. A carbon engine computes net removals using the official India CCTS methodology (BM FR05.001).
5. A verifier reviews the report. If approved, its fingerprint (hash) is anchored on the blockchain.
6. Credits are issued as tokens, which can be traded and finally retired (used up) with a public certificate.

**Extra features from the original pitch:** AI fraud detection (photo and satellite checks), a
stewardship score that rewards long-term care, forward funding (a reverse auction where buyers pay for
future credits), and a marketplace.

### Why we rebuilt it

The old prototype looked complete but was not honest. We kept only its screen and role ideas.

| Old prototype | Problem |
|---|---|
| Random Forest trained on about 38 hand-typed rows | Meaningless model |
| Printed a hard-coded "92% accuracy" | Fabricated metric |
| Biomass = NDVI x 100, then a fake tree-diameter step | Not real science |
| Indices computed on whole-tile averages of raw numbers | EVI above 1, impossible |
| Backend never called the ML; "analysis" was a filename mock; `random.uniform` for reports | Fake pipeline |
| Contract name did not exist, so every chain call fell back to fake `DEMO_TX_*` hashes | Fake blockchain |
| Open mint endpoint, no access control on project status | Insecurity |
| Training data and model weights lost | Not reproducible |

**The rules that came out of this** (also in `CLAUDE.md`):

- Never fabricate metrics. Never fall back to fake transaction hashes or IDs. Show failures as errors.
- Every carbon figure must be traceable to inputs, a methodology profile and model versions.
- Never commit to `main`: use a feature branch and a pull request.
- The old code lives in `_legacy/`, git-ignored, for reference only.

---

## 2. The big picture: how the pieces fit

```
 Mobile app (Expo)         Web dashboard (Next.js)          Public (no login)
 field capture, offline    console, verifier, registry,     registry, certificates,
        |                  marketplace, Explore Demo        verify-by-hash
        |                         |                              |
        +-----------+-------------+------------------------------+
                    v
              API  (FastAPI)  <---->  PostgreSQL + PostGIS   (projects, plots, reports, audit log)
                    |
                    +--> Valkey queue --> Worker (Celery)
                    |                        |-- evidence checks (photos, EXIF, geofence, duplicates)
                    |                        |-- satellite pipeline (Sentinel-1/2 via STAC)
                    |                        |-- ML (ecosystem classifier, photo verifier)
                    |                        |-- report building (hash, PDF, CSV, GeoJSON)
                    |                        '-- chain jobs (relayer)
                    |
                    +--> Object storage (SeaweedFS in dev, Cloudflare R2 in prod): photos, PDFs, models
                    |
                    '--> Blockchain (Polygon Amoy testnet; local Anvil in dev)
                         AquaRegistry, AquaCredit (ERC-1155), AquaMarketplace,
                         AquaForwardFund, MockUSDC
```

Think of it as a factory line. The API is the front desk: it checks who you are and what you may do. The
worker is the back office that does slow jobs (satellite downloads, ML, PDFs, chain transactions). The
database is the filing cabinet. The blockchain is a public notice board nobody can quietly rewrite.

---

## 3. Technology used, and why

### Backend
| Tech | What it does here | Why chosen |
|---|---|---|
| **Python 3.12** with **uv** | Language and package manager | Shares geo and ML code between API and worker; uv gives reproducible installs |
| **FastAPI** | The API | Fast, typed, auto-generates OpenAPI docs at `/docs` |
| **SQLAlchemy 2 (async)**, **Alembic** | Database access and migrations | Mature, safe schema changes (14 migrations so far, head `0013_api_gaps`) |
| **PostgreSQL 17 + PostGIS** (image `imresamu/postgis:17-3.6`) | Data plus maps and geometry | Needed for overlap checks (no double-counting land); that image supports ARM, the official one did not |
| **GeoAlchemy2** | PostGIS from Python | Geometry columns and spatial queries |
| **Celery + Valkey** | Background jobs and queue | Slow work stays off the API; queues: default, geo, ml, chain, reports |
| **Argon2id, JWT, TOTP** | Passwords, tokens, two-factor | Modern, safe defaults |
| **structlog** | Logging | Structured logs |

### Satellite and ML
| Tech | What it does | Why |
|---|---|---|
| **STAC** (Planetary Computer, Earth Search, Copernicus fallback) | Finds Sentinel-1/2 images | Free, no quota lock-in, unlike Google Earth Engine |
| **rasterio, odc-stac, rio-tiler** | Read images, make map tiles | Standard geo tooling |
| **Sentinel-2** (optical) and **Sentinel-1** (radar) | Land cover and loss detection | Free, 10 m resolution; radar sees through cloud |
| **LightGBM** | Ecosystem classifier (chosen model) | Beat Random Forest and XGBoost in spatial cross-validation |
| **ONNX Runtime** | Runs the model in the worker | Small and fast on the ARM server |
| **BioCLIP ViT-B/16** (linear probe) | Photo verifier: is this really a mangrove? | Strong with little data; the original photo dataset was lost |
| **DVC, MLflow** | Data and experiment tracking | Reproducible training, artifacts versioned and pushed to a remote |
| **launchd runner** | Runs long ML jobs unattended | Restarts on failure and resumes, so session limits cannot lose work |

### Blockchain
| Tech | What it does | Why |
|---|---|---|
| **Solidity 0.8.30, OpenZeppelin 5** | Smart contracts | Industry standard, audited building blocks |
| **Foundry** (via Docker, `contracts/forge.sh`) | Build and test contracts | Fast; 93 tests, 100% coverage, invariant tests, Slither clean |
| **ERC-1155** | Credit token | One contract holds many "vintages" (credit batches per year) with burn for retirement |
| **web3.py + custodial HD relayer** | Backend signs transactions for NGOs | Field users need no crypto wallet knowledge |
| **Polygon Amoy testnet / Anvil** | Test networks | Free; Anvil (chain 31337) for local runs |

### Web and mobile
| Tech | Role |
|---|---|
| **Next.js 16, React 19, Tailwind 4** | Web dashboard and public registry |
| **MapLibre** | Interactive maps with our own Sentinel-2 imagery |
| **next-intl** | English, Hindi, Odia (Hindi and Odia are AI-drafted, not native-reviewed) |
| **Expo SDK 58 (React Native)** | Field app with SQLite offline queue |
| **pnpm 12 + Turborepo, Biome** | JS monorepo tooling, lint and format |
| **`@aquacreds/api-client`** | Typed client generated from the API's OpenAPI; CI fails if it drifts |
| **Vitest, Playwright, axe** | Unit, browser and accessibility tests |

### Infrastructure
| Tech | Role |
|---|---|
| **Docker + Compose** | Everything runs in containers; images build for linux/arm64 (the free Oracle VM is ARM) |
| **Caddy** | Web server with automatic HTTPS and the security headers (CSP) |
| **SeaweedFS / Cloudflare R2** | S3-compatible storage (dev / prod). MinIO avoided: its community images were discontinued |
| **GitHub Actions** | CI: lint, types, tests, compose smoke test, image builds, plus security scans |
| **pip-audit, Trivy, gitleaks** | Dependency, image and secret scanning |

---

## 4. The science and the ML, honestly

### Credits come from a formula, not from a model
Decision D6: credits are the **verified change** in stored carbon per monitoring period, computed by a
deterministic engine (`aqua_carbon`) following **BM FR05.001** equations 2-8 plus the AR-TOOL14 uncertainty
discount. It has 26 tests, including the official worked examples. There is no "ML credit prediction".
Satellite biomass alone can never issue credits (D13): measured **field plots are mandatory**, and ML is
used for stratification, area, survival, no-decrease checks and fraud detection.

Constants used: carbon fraction 0.47, soil carbon default 0.50 tC/ha/yr over 20 years, wood densities for
18 Indian mangrove species (GWDD), tCCC/lCCC credit units with expiry and reversal replacement.
Outputs are always labelled "estimated, pending accredited verification".

### The satellite classifier (v1.1)
1. **Labels:** 14,129 labelled points from public data (coastTrain, Global Mangrove Watch v3/v4) over three
   sites (Bhitarkanika, Pichavaram, Kori Creek). Classes: mangrove, water, terrestrial, cropland/aquaculture,
   tidal flat, built/bare. Salt marsh had only 15 points, so it is mapped but not trained.
2. **Features:** for each point, 12 dry-season Sentinel-2 scenes (Nov 2020 - Mar 2021) plus Copernicus DEM
   elevation. Extraction took about 9.5 hours and about 165,000 requests, using little RAM.
3. **Training:** Random Forest vs LightGBM vs XGBoost with **spatial cross-validation** (5 folds by
   geography) and a **held-out region** (Gulf of Kutch) to test generalisation honestly. LightGBM won and
   was exported to ONNX.
4. **Serving:** the worker downloads the model from storage and checks its SHA-256 before use.
5. **Versioning:** v1 kept for provenance; v1.1 re-extracted the features after the DEM fix and gave
   identical metrics. Model card and metrics JSON live in `ml/model_cards/`.

### The photo verifier
BioCLIP embeddings plus a linear probe trained on iNaturalist photos (CC0/CC-BY/CC-BY-NC), exported to ONNX
(345 MB). Observer-held-out test F1 was 0.988. It is **off by default** (`PHOTO_VERIFIER_ENABLED=false`)
until the model is uploaded to R2. It uses a third-party BioCLIP conversion; the official 599 MB weights are
an option if approved.

### Evidence and fraud checks (Phase 3)
Photos are checked for EXIF time and place, a single-use capture nonce within a time window, whether they
fall inside the project polygon (PostGIS), and exact and near duplicates (SHA-256 plus perceptual hash).
These combine into a trust score. Offline crews can pre-fetch batch nonces (up to 20, valid 72 hours); that
weaker proof costs 10 points and is shown as a warning.

### Stewardship score
`stewardship-v1`: continuity 0.40, survival 0.35, evidence 0.25, explained to the user. A high-severity loss
alert only clears when independent staff (not the project's own organisation) acknowledge it.

### The data we lost and what we learned
Original Sentinel tiles, photos and weights were lost, so training started from public datasets. We later
lost classifier outputs stored in `/tmp` (macOS cleans it). Fix: durable clones, early WIP pushes, a local
DVC store, and the unattended runner. We also found the Planetary Computer data API refuses the
Copernicus DEM, which would have made every elevation 0; the DEM is now read from COG files directly.

---

## 5. The blockchain part

| Contract | Purpose |
|---|---|
| **AquaRegistry** | Projects and their approved reports (hash anchored after verifier approval) |
| **AquaCredit** | ERC-1155 credits by vintage; issue and retire (burn) |
| **AquaMarketplace** | Listings and purchases, paid in mock USDC |
| **AquaForwardFund** | Reverse auction: buyers fund future credits; checks removal capacity and the stewardship gate |
| **MockUSDC** | Test payment token |

**Issuance flow:** report built (RFC 8785 canonical JSON, hashed) -> verifier approves -> `registerProject`,
`submitReport`, `approveReport`, `issue` on-chain -> credits appear in the public registry with a verify-by-
hash page. The relayer is nonce-safe, the indexer is reorg-safe, and block timestamps are stored.

**Custody (D8):** NGO wallets are custodial (HD wallet from a server secret) so field users need no crypto.
Buyers and admins are meant to use MetaMask; wallet-connected buy/list/retire is still "coming soon" in the UI.

**Honesty rule:** if the chain is unreachable the system returns an error. It never invents a transaction hash.

---

## 6. Security and trust

- **Login:** Argon2id passwords, 15-minute JWTs, rotating refresh tokens (reuse revokes the whole family),
  timing-equalised login, rate limits, security headers.
- **Roles:** user, verifier, registry_admin, plus organisation roles. Verifiers cannot review their own
  organisation's projects (conflict of interest). Staff must use TOTP MFA in production, with recovery codes.
- **Production fails closed (D23):** it refuses to start with a missing or dev `JWT_SECRET`,
  `RELAYER_MNEMONIC` or `MFA_ENCRYPTION_KEY`.
- **Tamper-evident audit log:** hash-chained, with a verify endpoint that detects edits.
- **Web auth (D30):** the browser never sees tokens. A server-side layer (BFF) holds httpOnly cookies, with
  CSRF protection and one safe refresh when tokens expire.
- **Map tiles (D32):** signed, expiring tile URLs, re-checked per request, because MapLibre cannot send
  auth headers.
- **Explore Demo (D31):** off by default; a separate token audience, read-only, an allow-list of readable
  endpoints, a synthetic-only organisation, rate limits, and a test that walks every route so new routes
  cannot leak.
- **Privacy:** public reports are allow-listed; exact plot coordinates and CSV are never public (D28).
- **Supply chain:** pip-audit, Trivy and gitleaks run in CI; advisories were fixed by lockfile bumps.

---

## 7. How the work was done

### Method
- **Plan first:** research notes, datasets, tech-stack alternatives and an implementation plan were written
  before code.
- **Small PRs:** about 50 pull requests, one feature each, every one on a branch, tested, CI-green, merged.
- **Parallel workers:** independent agents each worked in their own git clone, on separate features. The
  coordinator (me) rebased, ran the full tests, built images, checked CI and merged one at a time. Model
  choice per task (D37): Sonnet for well-specified pages and docs, Opus for security, migrations and ML design.
- **Decision log:** every important choice is an entry (D1-D37) in `CONTEXT.md` with its reason.

### Timeline
| Date | What landed |
|---|---|
| 2026-10-19 | Audit of the old code, research, repo scaffold. Phase 0: uv workspace, API, worker, Docker, Compose, CI. Phase 2: users, orgs, projects, PostGIS, tenure documents, audit log. Carbon engine, smart contracts, evidence backend. Geo/ML data pipeline and 14,129 labels |
| 2026-10-20 | Deploy setup, geo pipeline, chain backend, MRV reports; then public registry, forward fund, verifier workflow, hardening and MFA, stewardship, STAC fallback and Sentinel-1. Feature extraction finished |
| 2026-10-21 | Classifier v1, SAR observations, marketplace API, certificates, end-to-end test and demo seed, notifications, design direction |
| 2026-10-28 | Signed map tiles, web app foundation, Explore Demo, Expo field app, launchd runner, photo verifier, classifier v1.1, demo moved onto real mangroves |
| 2026-10-30 | API gap-filling, then the web console: MRV reports, AI verification, overview, community, notifications, account security, i18n, registry, blockchain records, marketplace, public registry; web added to production compose |
| 2026-10-31 | Whole system run locally with seeded demo data (2,389 credits on a local chain) |

### Problems hit and how each was fixed
| Problem | Fix |
|---|---|
| A parallel agent's worktrees pointed at the wrong repo (the home-directory repo) | Caught before any push; workers now use fresh clones of AquaCreds (D19) |
| Stacked PRs merged into the wrong branch twice | All PRs target `main` directly |
| Migrations from parallel PRs branched from the same head | Distinct revision ids plus a merge revision (D22) |
| Production compose silently lacked the relayer secret | Fail-closed secrets and `${VAR:?}` (D23) |
| Constraint name doubled by the naming convention (migration 0008) | Bare names; the prefix is added automatically |
| Earth Search C1 treated as harmonised, so reflectance was off by +0.1 | Fixed and verified on live items (D27) |
| Planetary Computer refused Copernicus DEM | Read DEM COGs directly; features identical |
| Work and data lost from `/tmp` | Durable clones, local DVC remote, unattended runner (D33) |
| Private repo ran out of free CI minutes | Repo made public again (D34) |
| `ERR_PNPM_IGNORED_BUILDS` in CI only | `allowBuilds` in `pnpm-workspace.yaml`; test with a clean install (D35) |
| Three console PRs conflicted on shared files | Merged main into each branch; rewrote the Playwright config |
| rasterio missing `libexpat1` in images | Installed explicitly in all images |
| Frequent pip-audit advisories | Lockfile bumps (#43, #50, #52) |
| Anvil unreachable from the host | The Foundry image runs `sh -c`, so pass the whole command as one string |

---

## 8. Run it locally

Everything below runs on one Mac with Docker.

1. Start data services: `docker compose up -d --wait db valkey s3`, then `uv run aqua-api migrate`.
2. Start the local chain: `docker run -d --name aquacreds-anvil -p 8545:8545 ghcr.io/foundry-rs/foundry:v1.7.0 "anvil --host 0.0.0.0 --chain-id 31337"`.
3. Deploy contracts with `contracts/forge.sh` (`script/Deploy.s.sol`) and put the addresses in `.env`
   (copy of `.env.example`; never commit it).
4. Seed the demo: `DEMO_PASSWORD="demo-pass-2026" uv run aqua-api seed-demo --with-chain`.
5. Start API, worker and web: `uv run uvicorn aqua_api.main:app --port 8000`; the Celery worker
   (`-A aqua_worker.celery_app`, queues default,ml,reports,chain,geo); and from `apps/web`, the Next dev
   server on port 3000 with `AQUA_API_URL=http://localhost:8000 FEATURE_DEMO=1 FEATURE_TILES=1 COOKIE_INSECURE=1`.

| What | Address |
|---|---|
| Web app and Explore Demo | http://localhost:3000 |
| Console sign-in | http://localhost:3000/console/login |
| Public registry | http://localhost:3000/registry |
| API docs | http://localhost:8000/docs |

Demo logins use the password `demo-pass-2026`: `demo.admin.s42@example.org`, `demo.verifier.s42@example.org`,
`demo.registry.s42@example.org`, `demo.field.s42@example.org`, `demo.buyer.s42@example.org`.

**What is real and what is synthetic.** The boundary sits on real Bhitarkanika mangroves (about 416 ha,
99.7% mangrove by GMW 2020). The project, users, photos and satellite observations are synthetic and labelled
so. The carbon figures come from the real engine run on invented inputs. Credits are on a local test chain.

**Stop everything:** `pkill -f "uvicorn aqua_api"; pkill -f "celery -A aqua_worker"; pkill -f "next dev"`,
then `docker compose down; docker rm -f aquacreds-anvil`.

---

## 9. What is not done yet

**Needs a card or keys (the owner has no card for now)**
- Cloudflare R2 keys: publish classifier v1.1 and the photo model, and use R2 as the DVC remote.
- An Amoy RPC key plus faucet POL: a real testnet end-to-end run.
- The Oracle Always Free VM and two DNS records: hosted deployment. Card-free option: a Cloudflare Tunnel
  from the Mac while it is on.

**Product gaps**
- Web and mobile apps do not yet use the newest API endpoints (stats, `/models`, evidence points, batch
  nonces, idempotency keys).
- Wallet-connected buying, listing and retiring (UI says "coming soon").
- Photo thumbnails, a submitter field, member removal and invitation, a verifier review queue, a tenure
  document checklist, MFA setup in the web UI.
- Hindi and Odia need native-speaker review.
- Mobile: capturing offline needs a nonce fetched beforehand; not yet tested on a real device; Android drops a
  timezone EXIF tag.
- The project area check is a rough India coastal bounding box, not a coastline buffer.
- Demo mode in production needs a demo organisation seeded outside the stack.
- Classification map shows "Not classified yet" locally until the model is in local storage.

**Science to verify**
- The CCTS Detailed Procedure details, BM-T-AR-0004 text, mangrove root-shoot ratios and Komiyama belowground
  coefficients, and IPCC Wetlands Supplement factors.

**Small follow-ups**
- Fix the Anvil command in `docs/DEMO.md` (single-string form above) and add a one-command `make demo`.
- Consider enabling Dependabot security updates.

---

## 10. Where to find things in the repo

| Path | Contents |
|---|---|
| `packages/aqua_core`, `aqua_geo`, `aqua_carbon`, `aqua_mrv`, `aqua_chain` | Shared Python libraries: settings and DB, satellite, carbon maths, reports, blockchain |
| `services/api`, `services/worker` | The FastAPI app and the Celery worker |
| `contracts/` | Solidity contracts, tests, `forge.sh` |
| `ml/` | Training code, model cards, DVC pipeline |
| `apps/web`, `apps/mobile` | Next.js dashboard and Expo field app |
| `infra/` | Compose files, Caddy, deploy and smoke scripts, the launchd ML runner |
| `docs/` | Plan, tech stack, datasets, research notes, deployment, demo, design direction, this story |
