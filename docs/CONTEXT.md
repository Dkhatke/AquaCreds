# AquaCreds v2 — Project Context (living memory)

Read this first in every new session. Update the **Status log** and **Decisions** sections whenever
something changes.

---

## What this project is
AquaCreds is a blue-carbon **MRV (Monitoring, Reporting, Verification) + registry + marketplace**
platform for coastal ecosystems (mangroves first). It started as a Smart India Hackathon 2025 entry:
problem statement **SIH25038** "Blockchain-Based Blue Carbon Registry and MRV System", theme Clean & Green
Technology, team **VISSDOM** (team ID 82991).

Flow: NGO/panchayat registers a project boundary → field teams capture geotagged evidence in a mobile app →
satellite + ML pipeline maps the ecosystem and estimates biomass with uncertainty → carbon engine computes
net removals per methodology → accredited verifier reviews → report hash anchored on Polygon → credits
(ERC-1155 vintages) issued, traded, retired. Unique features from the pitch: AI fraud detection (photo +
satellite), loyalty for long-term stewardship, reverse auction (forward funding), tokenised marketplace.

## Who / what the user wants
- Owner: Dnyaneshwari Khatke (GitHub `Dkhatke`). Building a **production-grade MVP**, with emphasis on the
  **ML being real and well-validated**.
- Deployment must be **free**: Oracle Always Free ARM VM + Docker Compose + Caddy; Polygon Amoy testnet.
- Clients: **Expo mobile** (field) + **Next.js web** (verifier/admin/buyer/public).
- The original training data (Sentinel `.SAFE` tiles, `Dataset2` photos, `eco_model.h5`) is **lost**, so
  start from public datasets.
- The new GitHub repo must **not** carry legacy history. The legacy code sits in `_legacy/` (git-ignored,
  no `.git`) for reference only.

## Key documents
| File | Purpose |
|---|---|
| `docs/IMPLEMENTATION_PLAN.md` | Phase-wise plan, architecture, science design, deployment, risks |
| `docs/TECH_STACK.md` | Every tech choice with alternatives (incl. MobileNetV2 + Random Forest evaluation) |
| `docs/DATASETS.md` | Every dataset: use, source, licence, verification status |
| `docs/RESEARCH_NOTES.md` | Tenure, satellite timing, monitoring cadence, carbon calculation (BM FR05.001 + AR-TOOL14), worked example, plan changes R1–R9 |
| `requirements/` | Shareable requirement files + prerequisites (README) |
| `_legacy/aquacreds-system/` | Old SIH prototype (reference only; do not port its ML/carbon math) |

## Legacy audit (why we rebuilt)
- ML: RF trained on ~38 hand-typed rows; `model.py` printed a **hard-coded 92% accuracy** and fake
  report. Biomass = NDVI×100 → "canopy" → fake DBH → Komiyama per-tree equation without wood density,
  reported as t/ha. Indices computed on whole-tile means of raw DN (EVI > 1). CNN weights/dataset missing.
- Backend (FastAPI in `services/blockchain`, mixed with Node boilerplate): never called ML; analysis was
  a filename mock; `generate-mrv` used `random.uniform`; IPFS stub; contracts loaded as `CarbonRegistry`
  (doesn't exist) so all chain calls fell back to fake `DEMO_TX_*`; unauthenticated mint endpoint; `/users`
  route broken (missing import).
- Contracts: ERC-20 minted once per project, no retirement; `ProjectRegistry.updateStatus` had no access
  control; deploy script used ethers v5 API with a v6 toolbox.
- Mobile (Expo 54): geotag photo stored in AsyncStorage only; hard-coded LAN IP.
- **Reuse:** UX flows/screens and role ideas only.

## Decisions (ADR-lite)
| # | Decision | Date | Why |
|---|---|---|---|
| D1 | Python FastAPI single backend; Celery + Valkey workers | 2026-10-19 | Shares geo/ML code |
| D2 | PostgreSQL 17 + PostGIS (not MongoDB) | 2026-10-19 | Overlap/double-counting checks, relational workflow |
| D3 | STAC (Planetary Computer → Earth Search → CDSE), not GEE, in production | 2026-10-19 | No quota dependency; GEE noncommercial tiers from Apr 2026 |
| D4 | Satellite classifier: LightGBM primary, RF baseline, AlphaEarth embeddings challenger, U-Net stretch | 2026-10-19 | See TECH_STACK §5.2 |
| D5 | Photo model: BioCLIP 2 embeddings/zero-shot primary, MobileNetV2 baseline, distil to MobileNetV4 if slow; ONNX serving | 2026-10-19 | Lost dataset; few-shot strength |
| D6 | Carbon credits = verified **change** per monitoring period via deterministic `aqua_carbon` engine; no ML "credit prediction" | 2026-10-19 | Scientific validity |
| D7 | ERC-1155 vintages + retire/burn; Foundry; OZ 5.x; mock USDC payments | 2026-10-19 | Registry semantics, testability |
| D8 | Custodial HD wallets + backend relayer for NGOs; MetaMask (wagmi/viem) for buyers/admins | 2026-10-19 | Field users need no crypto UX (pending user confirmation) |
| D9 | Object storage: R2 (prod) / SeaweedFS (dev); no MinIO | 2026-10-19 | MinIO community images discontinued Oct 2025 |
| D10 | Postgres image `imresamu/postgis:17-3.6` | 2026-10-19 | Official image is amd64 only; Oracle VM is ARM |
| D11 | Mangroves credited in v2.0; salt marsh mapped only; seagrass research layer only | 2026-10-19 | Data/method availability |
| D13 | Field sample plots are mandatory for crediting; ML = stratification + model-assisted estimation + area/survival + no-decrease + fraud. Satellite AGB never issues credits alone | 2026-10-19 | AR-TOOL14: ex-post biomass from plots, RS only "in conjunction with" plots |
| D14 | CCTS profile uses tCCC/lCCC with expiry and reversal replacement (no buffer pool); uncertainty-discount table; CF 0.47; SOC default 0.50 tC/ha/yr × 20 yr | 2026-10-19 | BM FR05.001 Eq. 1–8 |
| D15 | Tenure & authorisation evidence pack (forest/revenue/port/panchayat/private + FPIC + CRZ + carbon-rights agreement) replaces "land registry check" | 2026-10-19 | Intertidal land has no ordinary registry; mostly state-controlled |
| D16 | Cadence: nightly ingestion + alerts; photo points 6-monthly; annual internal report; verification/issuance every 2–5 yr (first at yr 3–5) | 2026-10-19 | Ex-post credits; saplings invisible at 10 m for years |
| D17 | Git workflow: never commit to `main`; feature branch + PR, user merges | 2026-10-19 | User instruction |
| D18 | Local dev env = uv-managed `.venv` (Python 3.12) at repo root with ml-training + dev requirements; services run in Docker | 2026-10-19 | Isolated from system Python 3.9; reproducible |
| D19 | Parallel work: each subagent works in its own fresh `git clone` of Dkhatke/AquaCreds (NOT Agent worktree isolation, which used the home-dir repo Dkhatke/Spectra-26). Coordinator integrates PRs one at a time: rebase → full tests → image build → CI → merge | 2026-10-20 | Wrong-repo incident; avoids cascading conflicts |
| D20 | Repo is **public** again (user choice) for unlimited Actions minutes and public GHCR images | 2026-10-20 | Private repo capped CI at 2,000 min/month |
| D21 | Production images: Dockerfile base stage copies every workspace member's pyproject; api = core + chain; worker = core + chain + geo (aqua-geo is an explicit worker dependency) | 2026-10-20 | Images failed to resolve the workspace once new packages were added |
| D22 | Migrations from parallel PRs branch off the same head with distinct revision ids (`0006_mrv_reports`, `0006_user_mfa`); the coordinator adds a merge revision when the second lands | 2026-10-20 | Avoids renumbering migrations inside open PRs |
| D23 | Production secrets fail closed: `assert_production_safe` refuses missing/dev `JWT_SECRET`, `RELAYER_MNEMONIC`, `MFA_ENCRYPTION_KEY`; `compose.prod.yaml` passes them with `${VAR:?}` | 2026-10-20 | Prod compose was silently missing the relayer mnemonic |
| D24 | Staff (verifier, registry_admin) need TOTP MFA in production; verifiers cannot review projects of their own organisation | 2026-10-20 | Conflict of interest + account takeover risk |
| D25 | JS tooling: pnpm 12 + Turborepo; Biome (not ESLint, which lacks TS 7 support); typed client `@aquacreds/api-client` generated from OpenAPI, CI fails on drift | 2026-10-20 | One source of truth for web + Expo |
| D12 | Methodology profiles: India CCTS `BM FR05.001` + Verra `VM0033 v2.1`; label outputs "estimated, pending accredited verification" | 2026-10-19 | Regulatory honesty |

## Environment facts
- Workspace: `/Users/dnyaneshwarivijaykumarkhatke/Documents/Aquacreds_final` (its own git repo, branch
  `main`). The user's home directory is *also* a git repo, so always run git commands inside this folder.
- Local machine: macOS, Docker 29.7, Node 26.9, system Python 3.9.6 (too old). Install **uv** to get Python
  3.12.
- `.agents/` and `.cursor/` contain editor notifier hooks: git-ignored, leave them alone.
- Versions checked 2026-10-19: FastAPI 0.146, SQLAlchemy 2.1, Pydantic 2.14, Celery 5.6, web3.py 8.0,
  rasterio 1.5, odc-stac 0.5, scikit-learn 1.9, LightGBM 4.7, PyTorch 2.14, ONNX Runtime 1.31, MLflow 3.17,
  DVC 3.67, Next.js 16.4, React 19.3, Expo SDK 58, Tailwind 4.3, wagmi 3.7, viem 2.57, MapLibre 6.15,
  OpenZeppelin 5.6, Foundry 1.8.5, Hardhat 3.20, TypeScript 7.0.

## Open questions for the user
1. Confirm D8 (custodial NGO wallets).
2. Team size / timeline (plan assumes 2–3 devs, ~12–14 weeks with parallel tracks).

## Open verification tasks
- CCTS Detailed Procedure v1: tenure-document checklist, crediting-period length, verification frequency.
- BM-T-AR-0004 text (assumed = AR-TOOL14 v04.1; BEE site blocks automated download).
- Mangrove root-shoot ratios for project trees (AR-AM0014 / IPCC Wetlands Supplement).
- Komiyama 2005 BGB coefficients (AGB 0.251·ρ·D^2.46 confirmed via secondary source).
- GMW v4.0 official download host; coastTrain and Worthington data repositories.
- GEDI L4A current version; ESA CCI Biomass latest version.
- IPCC Wetlands Supplement Tier-1 mangrove factors (page refs) for methodology profiles.
- Oracle Always Free limits on the user's tenancy (4/24 vs 2/12).
- Planetary Computer `sentinel-1-rtc` access terms.

## Status log
| Date | What happened |
|---|---|
| 2026-10-19 | Audited legacy repo; researched datasets/stack; created new repo scaffold with docs (plan, stack, datasets, context) and `requirements/`. Nothing committed yet. |
| 2026-10-19 | Pushed initial scaffold to new **public** repo `github.com/Dkhatke/AquaCreds` (`origin`, branch `main`, commit e3a3bd8). Repo-local git identity: `Dkhatke <khatkednyaneshwari06@gmail.com>` (no global git identity on this Mac; don't use the "Mahi Mahalle" identity from the home repo). Next: Phase 0. |
| 2026-10-19 | Branch `docs/research-verification`: deep research on tenure, satellite timing, monitoring cadence, carbon calculation → `docs/RESEARCH_NOTES.md`; plan revised (R1–R9, decisions D13–D18). Installed uv + pango via Homebrew; created `.venv` (Python 3.12.15, 427 pkgs from ml-training + dev; 2.1 GB; torch MPS available; all key imports OK). Next: merge PR, then Phase 0 on `feat/phase-0-foundations`. |
| 2026-10-19 | PR #1 merged. PR #2 (`docs/design-direction`): Google Stitch master prompt (`docs/design/STITCH_MASTER_PROMPT.md`), awaiting the user's chosen direction. |
| 2026-10-19 | Phase 0 on `feat/phase-0-foundations`: uv workspace (root `pyproject.toml` + `uv.lock`, groups `dev`/`ml`, CPU torch index on Linux); `packages/aqua_core` (settings w/ prod-safety check, structlog, async SQLAlchemy + PostGIS check, S3 helpers); `services/api` (FastAPI factory, `/healthz`, `/readyz` [db/redis/s3], `POST /api/v1/system/ping-worker`, Alembic `0001_enable_postgis`, `aqua-api` CLI migrate/revision/bootstrap); `services/worker` (Celery, queues default/geo/ml/chain/reports, `aqua.system.ping`); multi-stage multi-arch Dockerfile (targets api/worker/dev; non-root); root `compose.yaml` (imresamu/postgis:17-3.6, valkey:8.1, seaweedfs:4.48, migrate, api w/ reload, worker w/ watchfiles); Makefile; `.env.example`; CI (lint/types/tests, compose smoke, image build; multi-arch push to GHCR on main); pre-commit (ruff, gitleaks). **Verified locally:** 12 tests pass, ruff/mypy clean, stack healthy, readyz all green, worker round trip OK, prod images run as non-root (api 419 MB, worker 377 MB). JS tooling (pnpm/turbo) deferred to the web/mobile phase, when apps exist. |
| 2026-10-19 | Phase 2 (part 1) on `feat/phase-2-core-backend` (stacked on PR #3): models (users, orgs, memberships, rotating refresh tokens, projects w/ PostGIS MULTIPOLYGON + GiST, tenure documents, hash-chained audit log) + migration `0002_identity_projects` (`alembic check` clean); auth (Argon2id, 15-min JWT, refresh rotation with family revocation on reuse, timing-equalised login); RBAC (platform roles user/verifier/registry_admin + org roles admin/field_agent/member); orgs + members; projects (geodesic area via PostGIS geography, India-coast bbox, invalid-geometry + area limits, cross-org overlap check w/ 0.01 ha tolerance); tenure docs via presigned PUT + server re-hash; submission rules (CCTS start >= 2025-01-01, >90% mangrove, <=10% soil disturbance, required docs per tenure type, overlap re-check); `/audit` + `/audit/verify` (tamper detection); CLI `create-user` (validates email like the API). 33 tests (real PostGIS via testcontainers), 85% coverage (greenlet-aware). Live e2e verified with SeaweedFS presigned uploads. Dev registry admin: registry.admin@example.org (local dev DB only). |
| 2026-10-19 | Merged so far: #1–#5, #8 (main has Phase 0 + Phase 2; carbon engine #5 is on `feat/phase-0-foundations`, carried to main by **#10**). **All open PRs now target `main` directly** (stacked bases caused two "merged into the wrong branch" incidents). Open: #10 (carbon → main, merge first), #6 Phase 1 geo/ML data, #7 contracts, #9 Phase 3 evidence. |
| 2026-10-19 | **Phase 1:** `aqua_geo` (STAC → masked reflectance → composites → indices); downloads via DVC (coastTrain 7 MB, GMW v4 2020 180 MB, GMW v3 1996 68.5 MB; MD5-verified; 500 MB/file + 10 GB free-disk guards); site composites Bhitarkanika / Pichavaram / Kori Creek (≤1.06 GB RAM); **14,129 training labels** (mangrove 4,801 · water 4,763 · terrestrial 2,662 · cropland/aquaculture 1,396 · tidal flat 282 · built/bare 210 · salt marsh 15 = not trainable), 5 spatial folds + Gulf of Kutch held out. Lessons: all remote reads need GDAL/STAC timeouts (`aqua_geo.network`); Planetary Computer SAS tokens expire (~45 min), so re-sign per read. Open question: only 55/17,169 1996-sampled mangrove points absent in GMW 2020; spot-check on imagery. |
| 2026-10-19 | **Carbon engine** (#5): BM FR05.001 eq. 2–8 + AR-TOOL14 discount; tCCC/lCCC/reversal; 26 tests incl. AR-TOOL14 worked examples. **Contracts** (#7): Registry/Credit (ERC-1155)/Marketplace/MockUSDC; 48 tests, 100% coverage, invariants, Slither clean; Foundry via pinned Docker image (`contracts/forge.sh`). **Phase 3 evidence backend** (#9): periods, capture nonces, photo submissions, plots/measurements; worker checks (EXIF, nonce window, PostGIS geofence, SHA-256 + pHash duplicates) → trust score; 84 tests total on that branch. |
| 2026-10-19 | **Next:** extract Sentinel-1/2 features at label points → train RF baseline vs LightGBM with spatial CV + held-out Gulf of Kutch (Phase 1d); then Phase 4 satellite pipeline in the worker. Mobile/web UI waits for the user's Stitch direction choice. User preferences: never commit to main; stop idle containers; keep memory/disk use bounded. |
| 2026-10-20 | **Parallel round integrated** into main: #13 deploy (Oracle Always Free compose + Caddy, deploy/backup/restore, deploy workflow, `docs/DEPLOYMENT.md`), #11 Phase 4 geo pipeline (AnalysisRun/SceneObservation/Alert, backfill + nightly ingest + composites, loss alerts), #14 Phase 6 chain backend (`aqua_chain`: ABIs, HD signer + org wallets, nonce-safe relayer, reorg-safe indexer, chain API; 12 Anvil integration tests), #12 MRV reports (`aqua_mrv`: RFC 8785 hashed record, PDF/CSV/GeoJSON, GWDD wood densities for 18 Indian mangrove species). Coordinator added migration `0005_merge_geo_chain` (merges `0004_geo_timeseries` + `0004_chain`) and fixed the Dockerfile. Full suite on main: 259 tests, 91.2% coverage. |
| 2026-10-20 | Incidents: (1) Agent worktree isolation created worktrees from the home repo (origin Dkhatke/Spectra-26); caught before any push; agents moved to scratchpad clones. (2) The permission classifier blocked subagent pushes of 3 branches; the user approved and the coordinator pushed (#12–#14). (3) ML agent hit the session rate limit; resumed 2026-10-20 09:41 IST. |
| 2026-10-20 | **Open items:** ML classifier PR (agent running; clone `scratchpad/ws-feat-ml-classifier`); `aqua.reports.*` worker task (worker doesn't depend on aqua_mrv yet; Pango libs already in image); verifier review endpoints + MRV report DB model; geo: runs killed by a hard time limit stay `running`, no fallback STAC provider, no Sentinel-1 yet; chain indexer not on the beat schedule yet; GWDD values taken from the BIOMASS R-package copy (not checksum-compared with Dryad); web/mobile UI waits for the Stitch direction choice. |
| 2026-10-20 | **Round 3 (parallel workers, own clones):** PRs opened, all CI green, awaiting the user's merge (the permission classifier blocks the coordinator's `gh pr merge`): #17 Phase 8 public registry (no-login `/api/v1/public`: projects, time series, vintages, retirements + QR, verify-hash; privacy allow-list tests), #18 `AquaForwardFund` reverse auction (93 Foundry tests, 100% coverage, invariants), #19 Phase 5b MRV report model + `aqua.reports.build_report` + verifier review (conflict-of-interest check) + `aqua.reports.anchor` (submit → approve on-chain) + `/reports/{id}/verify`, #20 hardening (rate limits, TOTP MFA, security headers, stale-run reaper, beat: indexer + reaper, pip-audit/Trivy/gitleaks CI), #16 frontend foundations (pnpm/turbo, `@aquacreds/api-client`, drift check). |
| 2026-10-20 | Coordinator fixes: prod compose never passed `RELAYER_MNEMONIC` (main cannot boot in production until #20) and passed the RPC as `AMOY_RPC_URL` (ignored by settings) → now `CHAIN_RPC_URL` + contract addresses; verified with `infra/scripts/smoke-prod-local.sh` (SMOKE PASSED incl. rollback). #19: field evidence accepted while a period is `changes_requested`. **Merge order:** #17 → #18 → #19 → #20 (+ merge revision `0006_mrv_reports` ⨝ `0006_user_mfa`) → #16 (regenerate the API client last). |
| 2026-10-20 | **In progress:** ML classifier feature extraction (Sentinel-2 at 14,129 label points; ~140/712 H3 cells at 14:30 IST, ~260 MB RAM), then RF vs LightGBM with spatial CV + held-out Gulf of Kutch; forward-fund backend (indexing, read API, custodial offer relaying with a removal-capacity check; depends on #18); stewardship score (computed, versioned, explainable; depends on #19). **Open:** recovery-code regeneration endpoint; MFA not yet required for `can_read_all_projects`; `block_time` missing for buyer-signed retirements (indexer stores no block timestamp); photo verifier (Phase 1f); Sentinel-1 + fallback STAC provider; web/mobile UI awaits the Stitch direction. |
