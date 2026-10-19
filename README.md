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

## Documentation
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md): phases, architecture, science, deployment
- [Tech stack evaluation](docs/TECH_STACK.md): choices and alternatives
- [Dataset catalogue](docs/DATASETS.md): data sources, licences, usage
- [Project context](docs/CONTEXT.md): decisions and status log
- [Requirements & prerequisites](requirements/README.md)

## Licence
To be decided (MIT proposed for code; datasets keep their own licences, see docs/DATASETS.md).
