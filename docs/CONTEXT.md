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
