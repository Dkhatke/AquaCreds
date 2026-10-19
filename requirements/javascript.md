# JavaScript / TypeScript / Solidity packages

Latest stable versions checked on 2026-10-19. The real pins will live in each app's `package.json` and
the root `pnpm-lock.yaml`. Install everything with `pnpm install` from the repo root once the apps exist.

## Monorepo tooling
| Package | Version |
|---|---|
| pnpm | 12.14 |
| turbo | 2.11 |
| typescript | 7.0 |
| openapi-typescript | 7.13 |
| openapi-fetch | 0.17 |
| zod | 4.6 |

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
