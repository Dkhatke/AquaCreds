# AquaCreds v2 — Requirements & Setup Prerequisites

This folder lists everything needed to run, develop or train AquaCreds v2. It's self-contained, so you can
share it on its own.

| File | What it's for | Who needs it |
|---|---|---|
| [`python/api.txt`](python/api.txt) | FastAPI backend | Backend devs, Docker image |
| [`python/worker.txt`](python/worker.txt) | Celery worker: satellite pipeline, ML inference, chain relayer, PDF reports (includes `api.txt`) | Backend/ML devs, Docker image |
| [`python/ml-training.txt`](python/ml-training.txt) | ML research & training: PyTorch, BioCLIP, LightGBM, MLflow, DVC (includes `worker.txt`) | ML devs (laptop, Kaggle, Colab) |
| [`python/dev.txt`](python/dev.txt) | Lint, type-check, tests | All Python devs |
| [`javascript.md`](javascript.md) | Web (Next.js), mobile (Expo) and contracts (Foundry/OpenZeppelin) packages | Frontend/contract devs |

> All four Python sets were resolved together on **2026-10-19** for Python 3.12 on Linux x86_64,
> Linux ARM64 (the Oracle VM) and macOS 14+ (Apple Silicon). Once the code exists, `uv.lock` and
> `pnpm-lock.yaml` hold the exact versions; these files are for setup and sharing.

---

## 1. System tools

| Tool | Version | Install (macOS) | Install (Ubuntu/Debian) | Needed for |
|---|---|---|---|---|
| **Git** | ≥ 2.40 | `xcode-select --install` | `sudo apt install git` | Everything |
| **Docker** (Desktop or Engine) + Compose v2 | Docker ≥ 27 | [Docker Desktop](https://www.docker.com/products/docker-desktop/) | [Docker Engine docs](https://docs.docker.com/engine/install/ubuntu/) | Running the full stack |
| **uv** (Python manager; installs Python 3.12 for you) | ≥ 0.13 | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | same | All Python work |
| **Python** | 3.12 | `uv python install 3.12` | `uv python install 3.12` | API, worker, ML |
| **Node.js** | 22 LTS or newer (tested with 26) | `brew install node` or [nvm](https://github.com/nvm-sh/nvm) | nvm | Web, mobile |
| **pnpm** | ≥ 10 | `corepack enable && corepack prepare pnpm@latest --activate` | same | JS monorepo |
| **Foundry** (forge, cast, anvil) | ≥ 1.8 | `curl -L https://foundry.paradigm.xyz \| bash && foundryup` | same | Smart contracts |
| **DVC** | ≥ 3.67 | included in `ml-training.txt` | same | Dataset/model versioning |
| **QGIS** (optional) | ≥ 3.40 LTR | [qgis.org](https://qgis.org) | `sudo apt install qgis` | Labelling the gold test set, inspecting maps |
| **Android Studio** or a physical Android phone with **Expo Go** | latest | [developer.android.com](https://developer.android.com/studio) | same | Mobile testing |

**WeasyPrint system libraries** (PDF reports; only needed outside Docker):
- macOS: `brew install pango`
- Ubuntu: `sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libffi-dev`

> Note: macOS ships Python 3.9. **Do not** use it for this project. `uv` manages Python 3.12 separately
> without touching the system Python.

---

## 2. Installing Python requirements

```bash
# from the repo root
uv venv --python 3.12 .venv
source .venv/bin/activate

# pick what you need:
uv pip install -r requirements/python/api.txt -r requirements/python/dev.txt        # backend dev
uv pip install -r requirements/python/worker.txt -r requirements/python/dev.txt     # worker / geo dev
uv pip install -r requirements/python/ml-training.txt \
  --extra-index-url https://download.pytorch.org/whl/cpu \
  --index-strategy unsafe-best-match                                                # ML training (CPU torch)
```

On **Kaggle/Colab** (GPU), skip the CPU index so the CUDA build of PyTorch is used:
`pip install -r requirements/python/ml-training.txt`.

---

## 3. Hardware guidance

| Task | Minimum | Recommended |
|---|---|---|
| Run full stack locally (Docker) | 4 CPU, 8 GB RAM, 20 GB disk | 8 GB+ RAM allocated to Docker |
| Satellite pipeline for one project (≤ 5,000 ha) | 2 CPU, 4 GB RAM | 4 CPU, 8 GB |
| Tabular ML training (LightGBM/RF, India-wide samples) | 4 CPU, 8 GB | 8 CPU, 16 GB |
| Photo model / U-Net training | Free Kaggle or Colab GPU | Same |
| Production VM | Oracle Always Free Ampere A1 (2 OCPU / 12 GB minimum) | 4 OCPU / 24 GB |
| Disk for datasets (AOI = Indian coast) | 30 GB | 100 GB (DVC cache) |

---

## 4. Free accounts

| Account | Used for | Required? |
|---|---|---|
| GitHub | Code, Actions CI, GHCR image registry | Yes |
| NASA Earthdata | GEDI L4A, Simard mangrove biomass | ML training |
| Microsoft Planetary Computer (API key) | Higher STAC limits (works anonymously too) | Optional |
| Copernicus Data Space Ecosystem | Fallback Sentinel imagery | Optional |
| GBIF | iNaturalist image downloads for the photo model | ML training |
| Hugging Face | BioCLIP 2 / DINOv2 weights | ML training |
| Kaggle and/or Google Colab | Free GPU | ML training |
| Cloudflare | R2 storage (DVC remote, prod files, backups), optional DNS | Deployment |
| Oracle Cloud | Always Free ARM VM | Deployment |
| Alchemy or Infura | Polygon Amoy RPC endpoint | Blockchain |
| Reown (WalletConnect) | Project ID for wallet connections on the web | Web |
| Pinata | IPFS pinning (or self-host Kubo) | Blockchain |
| Sentry | Error tracking | Optional |
| Brevo or Resend | Transactional email | Deployment |
| Expo (EAS) | Android builds | Mobile |
| DuckDNS | Free subdomain | Deployment |
| MetaMask (browser extension) | Testnet wallet for buyer/admin flows | Testing |

---

## 5. Environment variables (preview)
A full `.env.example` arrives in Phase 0. Expected keys: `DATABASE_URL`, `REDIS_URL`, `S3_ENDPOINT`,
`S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `JWT_SECRET`, `AMOY_RPC_URL`, `RELAYER_MNEMONIC` (Docker
secret), `CONTRACT_*_ADDRESS`, `PINATA_JWT`, `PC_SDK_SUBSCRIPTION_KEY`, `EARTHDATA_USERNAME/PASSWORD`,
`SENTRY_DSN`, `SMTP_*`, `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_WC_PROJECT_ID`, `EXPO_PUBLIC_API_URL`.
**Never commit real values.**
