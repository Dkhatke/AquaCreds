# AquaCreds design direction (v1)

Chosen by the user on 2026-10-21. This replaces "awaiting a Stitch direction" in `CONTEXT.md`. It is the
contract for `apps/web` and `apps/mobile`.

**North star:** AquaCreds should read as an **environmental-intelligence instrument**, not as a crypto
dashboard or a generic SaaS template. The visual references were editorial nature-finance sites (very
large condensed headlines over real landscape imagery) and map-first geospatial dashboards (imagery fills
the screen; data panels float over it). We take that *spirit*, not their layouts or assets.

## 1. Brand tokens

| Token | Hex | Use |
|---|---|---|
| `deep-ocean` | `#0B2024` | dark surfaces (console background, map chrome) |
| `mangrove` | `#103A38` | raised dark surfaces, panels, nav |
| `lagoon-mint` | `#43C9A4` | primary actions, confirmed / verified states, focus rings |
| `sea-mist` | `#E9F0EC` | light surfaces (reports, marketplace, public pages) |
| `off-white` | `#F8FAF9` | page background on light pages |
| `review-amber` | `#E9AD54` | needs-review / pending states |
| `failed` | a muted red (choose one that passes contrast on both surfaces) | failed checks, rejections |

**Typography:**
- Headings: Space Grotesk (or Manrope).
- Body: Inter.
- Data values: Inter with **tabular numerals**: hectares, tCO₂e, hashes, trust scores.

All fonts are self-hosted via `next/font`; no runtime Google requests.

**UI principles:**
- Rounded cards with *restrained* borders and shadows.
- Dark surfaces for the monitoring console. Light surfaces for reports and marketplace browsing.
- Mint = primary action / confirmed check. Amber = review required. Red = failed check.
- Animation **only** to explain a process (e.g. the Collect → Analyse → Verify → Record flow) or to show
  activity (a job running). No decorative motion.

## 2. Not a typical frontend: concrete rules
- **No** generic SaaS hero (gradient blob + centred headline + three feature cards), no purple/indigo
  gradients, no glassmorphism, no emoji icons, no stock "blockchain" cubes or circuit art, no fake
  testimonial/logo walls, no invented impact statistics.
- **Imagery is real and ours**: true-colour Sentinel-2 composites and classification maps produced by the
  AquaCreds pipeline (Copernicus data, free licence, attribution "Contains modified Copernicus Sentinel
  data [year]"), or openly licensed photos with attribution recorded in the repo. Never copy assets from
  other sites.
- **Editorial type scale**: very large condensed/grotesk display headlines on the landing page; data
  pages use calm, dense, tabular layouts.
- **Map-first console**: the project map is the canvas; panels (evidence, time series, alerts) float over
  it or sit beside it. Numbers always show units and provenance (model version, source, date).
- **Honesty is part of the design**: every carbon figure is labelled "estimated — pending accredited
  verification; testnet registry units" where applicable; satellite figures are labelled "context only"
  where the API says so; demo data is visibly marked **Demo**.
- Accessibility: WCAG 2.2 AA contrast on both surfaces, keyboard navigation, visible focus, reduced-motion
  respected, `lang` attributes; Hindi/Odia i18n-ready strings (English first).

## 3. Site map
**Public website ("AquaCreds")**
- **Home.** Sections, in order:
  1. Nav: logo · Platform · Technology · Impact · Marketplace · **Explore Demo**.
  2. Hero over large mangrove imagery: one-line headline, one-line description, two CTAs.
  3. Platform preview: a real screenshot of the console with a project map and evidence status.
  4. How it works: Collect → Analyse → Verify → Record.
  5. Technology: AI/ML, satellite cross-validation, digital MRV, blockchain.
  6. Impact and stakeholders: restoration groups, communities, verifiers, buyers.
  7. Final CTA and footer: Explore the demo, methodology, contact.
- **How it works.** Methodology (CCTS BM FR05.001 + AR-TOOL14), what satellites can and cannot do, and
  how verification works.
- Public registry pages (existing `/api/v1/public`): projects, vintages, retirements + certificate.

**AquaCreds Console ("project workspace")**
- Overview
- Satellite monitoring
- AI verification (photo/evidence checks)
- Project registry
- MRV reports
- Blockchain records
- Carbon marketplace
- Community portal

**Explore Demo** (SIH judging requirement): a visible button opens the console on clearly labelled demo
data (the `seed-demo` dataset) with **no account creation**. Demo transactions are labelled as
demonstration transactions.

## 4. Page notes
**Page 1: Landing.** Objective: explain AquaCreds in five seconds and get judges into the demo.
- Suggested hero copy:
  - Eyebrow: "AI-powered blue carbon MRV".
  - Headline: "From coastal ecosystems to verifiable carbon evidence."
  - Body: "AquaCreds combines AI-driven ecosystem analysis, satellite intelligence and blockchain-backed
    records to make blue carbon monitoring more transparent."
  - CTAs: **Explore the demo ↗** (primary, mint) and **Discover the technology** (secondary, outlined;
    scrolls to the workflow).
- Keep the hero uncluttered; **do not display unsupported carbon-impact statistics.**

**Page 2: Monitoring dashboard.** Objective: show that AquaCreds is an environmental-intelligence
platform, not just a blockchain website.
- Full-bleed map (MapLibre) with the project boundary.
- Classification layer (mangrove / water / tidal flat …) with its model version and held-out accuracy.
- NDVI / MVI / SAR time series with clear-sky filtering.
- Alerts timeline.
- Evidence points (privacy-safe), and the stewardship score with its components.

Further pages follow the same tokens and principles. Each console section maps to existing API routers
(`projects`, `analysis`, `classifications`, `field`, `reports`, `chain`, `marketplace`, `forward-fund`,
`stewardship`, `notifications`, `public`).
