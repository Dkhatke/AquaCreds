# Demo data (`aqua-api seed-demo`)

A **synthetic**, clearly labelled demo project for UI work and SIH demos. It is not field data,
not satellite data and not a real project. Never show it as real.

## Run it

```bash
make sync                     # local .venv with every workspace package
make dev                      # dev stack: PostGIS, Valkey, SeaweedFS, migrate, api, worker
uv run aqua-api seed-demo     # uses .env (DATABASE_URL, S3_*); default seed 42
uv run aqua-api seed-demo --seed 7   # another, non-overlapping demo project
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
| Project `Demo Bhitarkanika Restoration (synthetic, seed N)`: a 0.02° square in Bhitarkanika, Odisha | name + description "DEMO - synthetic data …" |
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

## End-to-end test

`services/api/tests/test_e2e_lifecycle.py` runs the whole lifecycle (register → report → approve →
anchor → issue → list → buy → retire → public registry → stewardship → audit) on PostGIS + Anvil
containers: `uv run pytest -m e2e`.
