# JavaScript / TypeScript / Solidity packages

Exact pins live in the root `package.json`, each workspace package's `package.json`, and
`pnpm-lock.yaml` (checked 2026-10-20). Install everything with `pnpm install` from the repo root.
Node 24+ is required (CI uses Node 26). Node 25+ no longer ships corepack, so install pnpm with
`npm i -g pnpm@12.14.0` or run it via `npx pnpm@12.14.0`.

## Monorepo tooling (pinned)
| Package | Version | Where |
|---|---|---|
| pnpm | 12.14.0 | root `packageManager` |
| turbo | 2.11.9 | root |
| typescript | 7.0.2 | root, `packages/api-client` (typecheck) |
| typescript (codegen only) | 5.9.3 | `tools/openapi-codegen` (openapi-typescript needs the TS 5 compiler API; TS 7 has none) |
| @biomejs/biome | 2.5.15 | lint + format (ESLint's typescript-eslint does not support TS 7 yet) |
| openapi-typescript | 7.13.0 | `tools/openapi-codegen` |
| openapi-fetch | 0.17.0 | `packages/api-client` |
| vitest | 5.0.5 | `packages/api-client` |
| vite | 8.3.4 | `packages/api-client` (vitest peer) |
| @types/node | 26.6.5 | `packages/api-client` |
| zod | 4.6.7 | not yet installed: add to the apps (form validation) |

## Web: `apps/web` (Next.js)
| Package | Version | Purpose |
|---|---|---|
| next | 16.4 | Framework (App Router, SSR for the public registry) |
| react / react-dom | 19.3 | UI |
| tailwindcss | 4.3 | Styling (+ shadcn/ui components, copied into the repo) |
| @tanstack/react-query | 5.104 | Server state |
| maplibre-gl | 6.15 | Maps (open source, no billing) |
| react-map-gl | 8.1 | React bindings for MapLibre |
| @turf/turf | 7.4 | Client-side geometry (area, bbox) |
| recharts | 3.10 | Time-series charts |
| wagmi | 3.7 | Wallet hooks |
| viem | 2.57 | Ethereum client |
| @rainbow-me/rainbowkit | 2.2 | Wallet-connect UI |

## Mobile: `apps/mobile` (Expo SDK 58)
| Package | Version | Purpose |
|---|---|---|
| expo | 58.0 | SDK |
| expo-router | 58.0 | Navigation |
| expo-camera | 58.0 | In-app evidence capture |
| expo-location | 58.0 | GPS + accuracy |
| expo-sqlite | 58.0 | Offline submission queue |
| expo-image-manipulator | 58.0 | Resize/compress before upload |
| @maplibre/maplibre-react-native | latest compatible with SDK 58 | Boundary map |

## Smart contracts: `contracts/` (Foundry)
| Package / tool | Version |
|---|---|
| Foundry (forge/cast/anvil) | 1.8.5 |
| Solidity compiler | 0.8.x (latest supported by Foundry) |
| @openzeppelin/contracts | 5.6 (installed via `forge install` or npm) |
| Slither (Python tool, CI only) | latest |

Alternative considered: Hardhat 3.20 (see docs/TECH_STACK.md §4).
