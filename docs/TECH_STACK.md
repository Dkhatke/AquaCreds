# AquaCreds v2 — Tech Stack Evaluation

For each layer, this document lists what the SIH 2025 prototype/PPT used, what v2 uses, which alternatives
were considered, and why. Versions are the latest stable releases checked on **2026-10-19**. Exact pins
live in [`../requirements/`](../requirements/) and the lockfiles (`uv.lock`, `pnpm-lock.yaml`) once code
exists.

Legend: ✅ chosen · 🔁 kept as baseline / fallback · ❌ rejected

---

## 1. Summary table

| Layer | Legacy / PPT | v2 choice | Main reason |
|---|---|---|---|
| Field-photo model | MobileNetV2 (TensorFlow 2.12) | **BioCLIP 2 embeddings + linear head** (or a distilled MobileNetV4), PyTorch → ONNX Runtime | Strong with little labelled data; plant-aware; no dataset of our own needed to start |
| Satellite classifier | Random Forest on 38 rows | **LightGBM** on S2+S1+DEM+canopy-height features, with **AlphaEarth embeddings** as a challenger; RF kept as baseline | Gradient boosting usually beats RF on tabular EO features; embeddings excel with sparse labels |
| Segmentation | U-Net (planned, never built) | U-Net via segmentation_models_pytorch, **stretch goal** | Pixel classifier first; U-Net only if it beats the classifier on held-out F1 |
| Biomass | Formula on fake DBH | **LightGBM quantile regression + conformal intervals** trained on GEDI L4A / ESA CCI | Calibrated uncertainty is mandatory for carbon |
| ML framework | TensorFlow 2.12 | **PyTorch 2.14 + timm/open_clip**, scikit-learn 1.9, LightGBM 4.7 | Ecosystem momentum; BioCLIP/DINO/timm are PyTorch-native |
| Experiment tracking | none | **MLflow 3** + **DVC 3** | Reproducibility; data versioning |
| Inference runtime | Keras `.h5` in API process | **ONNX Runtime** + native LightGBM in a separate worker | Small, fast on ARM CPU, no torch in the production image |
| Satellite access | Manual `.SAFE` downloads (Windows paths) | **STAC: Planetary Computer → Earth Search → CDSE** via `odc-stac` | Automated, cloud-optimised, project-clipped reads; no GEE quota dependency |
| Backend | FastAPI + leftover Express boilerplate | **FastAPI 0.146** only, SQLAlchemy 2.1 async, Pydantic 2 | Python shares geo/ML code; one backend language |
| Database | MongoDB Atlas | **PostgreSQL 17 + PostGIS 3.6** | Spatial queries (overlap/double counting), relational workflow, transactions, audit |
| Jobs | FastAPI `BackgroundTasks` | **Celery 5.6 + Valkey 8** | Retries, routing, rate limits, separate worker processes |
| Object storage | Local `uploads/` folder committed to git | **Cloudflare R2** (prod), **SeaweedFS** (dev) | S3 API; MinIO community images discontinued Oct 2025 |
| Decentralised storage | Fake IPFS stub | **Pinata free / Kubo** pinning of report manifests | Real CIDs; no personal data on IPFS |
| Contracts toolchain | Hardhat (ethers v5 scripts on v6 toolbox; broken) | **Foundry 1.8** (+ OpenZeppelin 5.6) | Fast Solidity-native tests, fuzzing, invariants |
| Credit token | ERC-20, one mint per project | **ERC-1155**, one token ID per project vintage + retire/burn | Matches how real registries batch credits; prevents double counting |
| Chain | Polygon Amoy | **Polygon Amoy** (kept) via provider RPC | PPT alignment; free testnet |
| Wallet / auth | Web3Auth (planned), JWT | **Own JWT auth + custodial relayer for NGOs, wagmi/viem + RainbowKit for buyers** | No third-party lock-in; field users need no crypto knowledge |
| Web | (none; Expo only) | **Next.js 16** + Tailwind 4 + shadcn/ui + **MapLibre GL 6** + TanStack Query 5 | SSR public registry; free open-source maps |
| Mobile | Expo 54, `fetch` wrapper, hard-coded IP | **Expo SDK 58** + expo-router + expo-camera + offline queue (expo-sqlite) | Offline-first field capture |
| API typing | none | **OpenAPI → openapi-typescript client** shared by web & mobile | One source of truth for types |
| Reverse proxy | none | **Caddy 2** | Automatic HTTPS, tiny config |
| CI/CD | none | **GitHub Actions → GHCR → SSH deploy** | Free for public repos, arm64 runners |
| Hosting | local laptop | **Oracle Always Free ARM VM** + Docker Compose | $0 with enough RAM for geo jobs |
| Observability | `print()` | structlog + Sentry + Uptime Kuma + Flower | Free tiers, low overhead |

---

## 2. Backend

### API framework — ✅ FastAPI
- **Why:** the worker, geo pipeline and ML models are Python. One language means shared Pydantic schemas
  and domain code (`packages/aqua_core`). The OpenAPI output drives the TypeScript client.
- ❌ **NestJS / Express (Node):** would split the domain model across two languages; the legacy repo
  already showed the mess this creates.
- ❌ **Django + DRF:** batteries included (admin panel is attractive), but sync-first and heavier; FastAPI +
  a small custom admin in the web app is enough.
- 🔁 **Litestar:** technically excellent, smaller ecosystem.

### ORM / migrations — ✅ SQLAlchemy 2.1 (async) + GeoAlchemy2 + Alembic
- ❌ SQLModel: convenient but lags SQLAlchemy features and makes GeoAlchemy2 awkward.
- ❌ Raw asyncpg: no migrations or unit-of-work.

### Database — ✅ PostgreSQL 17 + PostGIS 3.6
- Project boundaries are polygons. Checking overlaps between projects (double-counting prevention),
  "is this photo inside the boundary", and area calculations in a projected CRS are native PostGIS
  operations.
- The workflow is relational (org → project → period → report → vintage → retirement) and needs
  transactions and foreign keys for integrity.
- ❌ **MongoDB** (legacy): `2dsphere` indexes exist but lack PostGIS's geometry toolkit. Schema drift
  caused the legacy "try ObjectId, then string, then id field" lookups.
- **ARM note:** use `imresamu/postgis:17-3.6` (multi-arch); the official `postgis/postgis` image is amd64
  only.

### Background jobs — ✅ Celery 5.6 + Valkey 8
- Separate queues (`geo`, `ml`, `chain`, `reports`) with independent concurrency. The `chain` queue runs at
  concurrency 1 for nonce ordering; `geo` is memory-capped.
- ❌ FastAPI BackgroundTasks (legacy): runs in the API process, no retries, lost on restart.
- 🔁 **Dramatiq:** simpler API, but a smaller ecosystem (Flower-equivalent monitoring is weaker).
- 🔁 **Procrastinate** (Postgres-based, no Redis): attractive for removing Valkey; reconsider if memory is
  tight.
- ❌ **Temporal:** excellent for long workflows, too heavy for a free VM.
- **Valkey vs Redis:** Valkey is the BSD-licensed Linux Foundation fork; it's a drop-in replacement.

### Auth — ✅ own implementation (Argon2id + JWT access/refresh + optional SIWE + admin TOTP)
- ❌ **Web3Auth / embedded wallets:** adds a paid-at-scale dependency; field users don't need
  self-custody in an MVP.
- 🔁 **Keycloak / Zitadel:** proper IdP, but a heavy container for a free VM.
- 🔁 **Supabase Auth / Clerk:** fast to adopt, but third-party lock-in and free-tier limits.

### Object storage — ✅ Cloudflare R2 (prod) / SeaweedFS (dev)
- ❌ **MinIO:** community Docker images stopped in Oct 2025 and the project entered maintenance mode.
- 🔁 **Garage** (Rust, lightweight S3): alternative for self-hosted prod storage on the VM.

### PDF reports — ✅ WeasyPrint (HTML/CSS → PDF via Jinja templates)
- 🔁 **Typst:** beautiful output, but an extra templating language to learn.

---

## 3. Geospatial pipeline

| Concern | ✅ Choice | Alternatives |
|---|---|---|
| Imagery catalogue | Microsoft Planetary Computer STAC (`sentinel-2-l2a`, `sentinel-1-rtc`, `cop-dem-glo-30`, `esa-worldcover`) | 🔁 Element 84 Earth Search v1 (`sentinel-2-l2a`, AWS; per-item `earthsearch:boa_offset_applied` decides the BOA offset; `sentinel-2-c1-l2a` is opt-in only, as its DNs keep the +1000 offset); 🔁 Copernicus Data Space Ecosystem (official ESA, quota-limited); ❌ Google Earth Engine for production (noncommercial quota tiers from Apr 2026, licensing for commercial use) |
| Loading | `odc-stac` + `pystac-client` → xarray/dask | 🔁 `stackstac` |
| Raster I/O | `rasterio` 1.5, `rioxarray`, `rio-cogeo` | — |
| Vector | `geopandas` 1.2, `shapely` 2.2, `pyproj`, `pyogrio` | — |
| Spatial CV grid | `h3` 4 | — |
| GEDI access | `earthaccess` (NASA Earthdata) | — |

**Google Earth Engine remains useful for research.** Notebooks may use it to explore AlphaEarth
embeddings, GMW v4 or quick visual checks. The production pipeline never depends on it.

---

## 4. Blockchain

### Toolchain — ✅ Foundry 1.8
- Tests in Solidity run in milliseconds; built-in fuzzing and invariant testing (critical for "never mint
  more than approved"); `forge script` deployments; `anvil` local chain for dev and CI.
- 🔁 **Hardhat 3** (3.20): now supports Solidity tests too and has a stronger TypeScript plugin ecosystem.
  It's a valid alternative. Foundry was chosen for fuzz/invariant ergonomics and speed.

### Token design — ✅ ERC-1155 vintages + retirement burn
- The legacy ERC-20 minted once per project and had no retirement, so the same tonne could be claimed
  repeatedly off-chain. ERC-1155 gives one fungible token ID **per project × monitoring period
  (vintage)**, which is how registries issue batches. Mint is capped at the approved net tCO₂e in the
  report record. `retire()` burns tokens and emits a certificate event.
- 🔁 **ERC-20 per vintage** (one contract per batch): simpler wallets, but contract sprawl.
- ✅ **ERC-721 (soulbound)** for the project deed, matching the PPT's "NFT for plantation records".

### Indexing — ✅ worker-based event poller writing to Postgres
- 🔁 **Ponder** (TypeScript indexer, self-hostable): a good upgrade if event volume grows.
- ❌ **The Graph** (PPT): the hosted service is gone, and the decentralised network costs GRT. Overkill.

### Gas sponsorship / custody — ✅ backend relayer with HD-derived org addresses
- 🔁 **ERC-4337 smart accounts + paymaster** (Pimlico/ZeroDev/Biconomy free tiers): better UX and
  self-custody path; planned as a post-MVP upgrade.
- ❌ **OpenZeppelin Defender:** hosted service sunset on 1 July 2026 (its open-source Relayer is AGPL and
  self-hostable, but heavy for this VM).

### Payment token — ✅ mock USDC (ERC-20) on Amoy
Stable pricing semantics. Native POL payments (legacy) mix gas and price volatility, and the legacy
`transfer()` pattern is unsafe with smart-contract recipients.

### Web wallet — ✅ wagmi 3 + viem 2 + RainbowKit
- ❌ **ethers v5** (legacy mobile app): superseded; viem is typed and lighter.

---

## 5. Machine learning

### 5.1 Field-photo verifier: what happens to MobileNetV2?

MobileNetV2 (2018) is a reasonable small CNN, but it is **no longer the best option**, and the legacy
`Dataset2` it was trained on is lost. Options evaluated:

| Option | Labelled data needed | Accuracy expectation | CPU (ARM) cost | Verdict |
|---|---|---|---|---|
| MobileNetV2 fine-tune (TF/Keras) | Thousands of labelled photos | Baseline | Very low (~3.5 M params) | 🔁 **Baseline only**: reproduce it to show improvement |
| MobileNetV4 / EfficientNetV2-S fine-tune (timm) | Thousands | Better than V2 at equal cost | Low | 🔁 Distillation target for production |
| ConvNeXt-V2 / ViT-B fine-tune | Thousands | High | Medium | ❌ Needs more data than we have |
| **BioCLIP 2** zero-shot (genus names as prompts) | **None** | Good for plant taxa; trained on TreeOfLife-200M | High (ViT-L/14) | ✅ Day-one model; also a **pseudo-labeller** |
| **BioCLIP 2 / DINOv2 embeddings + logistic regression** | Hundreds | Usually best few-shot | High (frozen encoder) | ✅ **Primary candidate** |
| Generic CLIP / SigLIP 2 zero-shot | None | Weaker on plant species | High | 🔁 Comparison |

**Plan:**
1. Build a labelled evaluation set from iNaturalist/GBIF (licence-filtered).
2. Benchmark all options with the same split.
3. Pick the winner by F1 at a fixed false-accept rate.
4. If the winning ViT exceeds ~3 s per image on the ARM VM, **distil** it into MobileNetV4 using
   BioCLIP pseudo-labels, keeping most of the accuracy at mobile-class cost.
5. Export to ONNX.

This keeps the original MobileNet idea, but in a form that can be measured and improved.

### 5.2 Satellite ecosystem classifier: what happens to the Random Forest?

Random Forest is a solid, well-understood baseline for land-cover mapping (Worthington et al. 2024 used RF
for the global tidal marsh map). It was not the algorithm that failed in the prototype; **the data and
evaluation did**. v2 keeps RF as the baseline and adds challengers:

| Option | Strengths | Weaknesses | Verdict |
|---|---|---|---|
| **Random Forest** | Robust to noisy labels, few hyperparameters, interpretable importances | Plateaus earlier; larger model files | 🔁 Baseline (must be beaten) |
| **LightGBM** | Often outperforms RF on tabular EO features (to be confirmed on our data); fast training/inference; small models; quantile objectives | More hyperparameters (tuned with Optuna) | ✅ **Primary** |
| XGBoost | Comparable to LightGBM | Slower on CPU for this use | 🔁 Comparison |
| **AlphaEarth 64-d embeddings + LightGBM/logistic** | Learned multi-sensor, multi-temporal features; state-of-the-art with sparse labels; CC-BY 4.0 | Annual only (2017–2025); an external dependency on Google's release cadence | ✅ Challenger. Adopt if it wins on the held-out region |
| Prithvi-EO-2.0 / Clay / TerraMind fine-tune | Strong geospatial foundation models | Needs GPU for fine-tuning and inference; heavy for the free VM | 🔁 Research track |
| **U-Net** (segmentation_models_pytorch) | Spatial context and sharp boundaries (the PPT's "pixel-level segmentation") | Needs chip labels (weak labels from GMW) and a GPU to train | 🔁 Stretch; adopt only if it wins on held-out F1 and runs on CPU in time |

**Decision rule:** best macro-F1 on the geographically held-out region (Gulf of Kutch), then on spatial
CV, then speed. All results go in the model card, including RF's score.

### 5.3 Biomass regression
- ✅ LightGBM quantile (P10/P50/P90) + **MAPIE** conformal calibration → honest intervals.
- 🔁 Random Forest regression / quantile regression forests: baseline.
- 🔁 Gaussian processes: good uncertainty, but don't scale to millions of pixels.
- **Labels:** GEDI L4A footprints + ESA CCI Biomass; independent check against literature field plots.

### 5.4 MLOps
- ✅ **DVC** (data + model versioning, R2 remote) + **MLflow** (runs, metrics, artifacts).
- ✅ **Optuna** for tuning; **SHAP** for explanations shown to verifiers ("why is this pixel mangrove").
- ✅ **Training compute:** local CPU for tabular models; **Kaggle Notebooks / Google Colab free GPUs**
  for image models and U-Net.
- ✅ **Serving:** ONNX Runtime + LightGBM in the worker image (no PyTorch in production, which keeps the
  image small for ARM).

---

## 6. Frontend

### Web — ✅ Next.js 16 (App Router) + TypeScript + Tailwind 4 + shadcn/ui
- SSR/SEO for the **public registry** pages (project, vintage, retirement certificate with QR).
- ✅ **MapLibre GL 6** (+ react-map-gl 8): open-source, no Mapbox billing. Basemap tiles from OpenFreeMap
  (free) or Esri World Imagery (attribution required); raster overlays served as COG tiles via `titiler`
  (optional) or pre-rendered PNG tiles from the worker.
- ✅ **TanStack Query 5** for data fetching; **zod 4** for form validation; **Recharts 3** for time
  series.
- 🔁 **Vite + React SPA:** simpler and hostable free on Cloudflare Pages, but no SSR for public pages.
- ❌ **Mapbox GL / Google Maps:** usage-based billing.

### Mobile — ✅ Expo SDK 58 (React Native) + expo-router
- `expo-camera` (in-app capture only), `expo-location` (accuracy recorded), `expo-sqlite` (offline queue),
  `expo-image-manipulator` (resize before upload), MapLibre React Native.
- ❌ **Flutter** (listed in the PPT): would mean a third language; the team already uses React Native.

### Shared — ✅ pnpm workspaces + Turborepo; TypeScript 7; generated `api-client`

---

## 7. Infrastructure

| Concern | ✅ Choice | Alternatives |
|---|---|---|
| Hosting | Oracle Always Free ARM VM | 🔁 Hetzner CAX11 (~€4/mo) if paying is acceptable; 🔁 split free PaaS (Vercel/Neon/Koyeb/Upstash); ❌ AWS/GCP managed (not free beyond trials) |
| Orchestration | Docker Compose v2 | ❌ Kubernetes/k3s for MVP (overhead on a small VM); 🔁 Coolify/Dokploy (self-hosted PaaS UI) as an optional convenience |
| Proxy/TLS | Caddy 2 | 🔁 Traefik; ❌ hand-managed Nginx + certbot |
| Registry | GHCR | — |
| Python packaging | **uv** (also installs Python 3.12) | ❌ pip + venv (slow, no lockfile); 🔁 Poetry |
| Secrets | `.env` on VM + Docker secrets; **sops + age** for encrypted env in repo | 🔁 Infisical (self-hosted) |
| Monitoring | Sentry free, Uptime Kuma, Flower, structlog JSON | 🔁 Grafana Cloud free (Prometheus/Loki) |
| Security scanning | gitleaks, pip-audit, Trivy, Slither, Dependabot | — |

---

## 8. Things deliberately *not* used, and why
- **Google Earth Engine in production:** quota/licence dependency. Fine for research notebooks.
- **The Graph:** cost and complexity for a single-app indexer.
- **MinIO:** unmaintained community distribution since late 2025.
- **Web3Auth:** unnecessary for custodial field users; adds vendor dependency.
- **TensorFlow:** the chosen models (BioCLIP, timm, smp) are PyTorch-native; ONNX covers serving.
- **Flutter:** consolidating on React Native/TypeScript.
- **Mainnet / real payments:** out of MVP scope; legal recognition needs accreditation.
