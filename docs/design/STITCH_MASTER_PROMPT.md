# Google Stitch — Master Prompt for AquaCreds

How to use:
1. In Stitch, start a new project and paste **Part 1 (master prompt)** as is. It sets the product,
   design language and rules.
2. Then generate screens one at a time with the **Part 2 screen prompts**. Stitch keeps the style from
   the master prompt across screens.
3. To compare looks, duplicate the project and append one **Part 3 direction modifier** to the master
   prompt.
4. Judge the results with the **Part 4 checklist**, then export the winner to Figma / HTML and drop the
   files into `docs/design/exports/`.

---

## Part 1 — Master prompt (paste this first)

```
Design the UI for "AquaCreds", a blue-carbon registry and MRV (monitoring, reporting, verification)
platform for mangrove restoration on India's coast. Coastal NGOs and village panchayats restore
mangroves; field teams measure sample plots and take geotagged photos in a mobile app; satellites
(Sentinel-1 radar and Sentinel-2 optical) and ML models map the forest; accredited verifiers review
evidence on a web dashboard; approved carbon credits are issued on a public blockchain registry and
bought and retired by companies.

THE FEELING: a precise scientific instrument crossed with an official public registry, rooted in the
estuary. Calm, credible, earthy, data-honest. It must feel like it was designed by a team that has
stood in a mangrove at low tide — not like a generic SaaS or crypto app.

STRICTLY AVOID: purple/indigo/violet gradients, neon crypto aesthetics, glassmorphism, blobs,
3D illustrations, stock people illustrations, emoji as icons, generic rounded white cards floating on
grey with heavy drop shadows, centered marketing hero sections, the Inter font, and dashboards made
only of KPI cards.

COLOR (use these exact tokens):
- Ink (primary text): #0F1E1C (deep mangrove black-green)
- Paper (app background): #F4F1EA (dry silt paper)
- Surface (panels): #FBFAF6
- Hairline borders: #D9D3C4
- Mangrove (primary action, brand): #1F5B45
- Tidal blue (links, water, info): #1C5D7A
- Silt brown (secondary accent, soil carbon): #9A7B4F
- Status — Verified: #2E7D4F · Pending: #C08A1E (turmeric) · Reversal/Alert: #B23A2E (laterite red)
  · Draft: #6B6F68
Satellite imagery and maps carry most of the colour; UI chrome stays quiet and flat.

TYPOGRAPHY (Google Fonts):
- UI and body: "IBM Plex Sans"
- All numbers, measurements, coordinates, IDs, hashes, tCO2e values: "IBM Plex Mono" with tabular
  figures
- Public-facing page titles and certificates only: "Fraunces" (serif)
Tight, editorial hierarchy; small caps labels for section headers (letter-spaced, 11–12px).

SHAPE & LAYOUT:
- Corner radius 4–6px max; 1px hairline borders instead of shadows; flat surfaces.
- Map-first layouts: the map or satellite image is the largest element on analytical screens.
- Dense but breathable data tables with right-aligned monospaced numbers and units (t CO2e, ha, Mg/ha).
- Status shown as small rectangular "stamp" badges with an uppercase label, not pills.
- Subtle motifs allowed: thin contour/tide lines, coordinate grid ticks on map edges, a tide-level
  indicator. Never decorative clutter.
- Icons: thin 1.5px line icons (Phosphor or Lucide style), never emoji.

DATA REALISM (use this kind of content, never lorem ipsum):
- Projects: "Bhitarkanika Fringe Restoration, Odisha" (142.6 ha), "Pichavaram Creek Replanting,
  Tamil Nadu" (58.2 ha), "Kori Creek Mudflat Revival, Gujarat" (310.0 ha).
- Species: Avicennia marina, Rhizophora mucronata, Sonneratia apetala, Ceriops tagal.
- Metrics: mangrove area 118.4 ha ± 6.1; mean biomass 24.1 Mg/ha (90% CI ±3.4); uncertainty 14% →
  discount 25%; net removals 5,074 t CO2e; verification period 2026–2031; credit type lCCC.
- Evidence checks: GPS inside boundary ✓, capture nonce valid ✓, duplicate photo check ✓, species
  model "Avicennia (0.91)", satellite consistency ⚠ "NDVI below 3-yr envelope".
- Hashes like 0x7f3a…c91e, coordinates like 20.6943° N, 86.9112° E, tide +0.4 m (falling).
- Roles: Field agent, NGO admin, Verifier, Registry admin, Buyer, Public.

PLATFORMS: a desktop web app (1440px) for verifiers, admins, buyers and the public registry, and an
Android mobile app (390×844) for field teams working outdoors in bright sun, often offline, sometimes
with wet hands. Mobile must use large touch targets (min 48px), high contrast, and show
offline/sync state clearly. Support Hindi labels alongside English where natural.

Start with the Verifier Review Workspace (desktop) described next.
```

---

## Part 2 — Screen prompts (one at a time, after the master prompt)

### W1 — Verifier review workspace (desktop, the hero screen)
```
Desktop screen: Verifier Review Workspace for "Bhitarkanika Fringe Restoration — Monitoring period
2026–2031". Left 60%: full-height map with Sentinel-2 true-colour basemap, the project boundary as a
thin outlined polygon, a semi-transparent mangrove classification overlay, and photo/plot pins
coloured by evidence trust (green verified, turmeric warning, red failed). Map has a before/after
swipe slider (2026 vs 2031), layer toggles (Class map, Biomass P50, Strata, Plots, Photos) and
coordinate ticks on the edges. Right 40%: tabbed evidence panel — Summary, Plots (table of 24 plots
with biomass Mg/ha and status), Photos (grid with check results), Calculation (step-by-step carbon
trace: biomass → uncertainty discount → ×0.47 → ×44/12 → minus baseline/leakage → 5,074 t CO2e
lCCC), Tenure (documents checklist: Forest Dept MoU, CRZ clearance, Panchayat resolution + consent).
Sticky footer actions: "Request changes", "Reject", primary "Approve report". Top bar with breadcrumb,
report hash in monospace and a status stamp "UNDER REVIEW".
```

### W2 — Project monitoring timeline (desktop)
```
Desktop screen: Project overview for "Pichavaram Creek Replanting". Header with project name in
Fraunces, location, area, methodology "CCTS BM FR05.001", status stamp "MONITORING". Main area: a
long horizontal timeline 2025→2035 showing planting date, photo-point visits every 6 months, annual
internal reports, and verification periods as bands with issued credit vintages. Below: two
time-series charts sharing the x-axis — NDVI (Sentinel-2, dots per clear scene with a seasonal
envelope band) and Sentinel-1 VH backscatter — with monsoon months shaded. A loss alert marker in
laterite red on one date with a note. Right rail: next actions (plot inventory due, photo points due
in 12 days), stewardship score 82/100 with a short explanation.
```

### W3 — Public registry: credit vintage page (desktop)
```
Desktop public page (no login) for credit vintage "AQC-BHK-2031-L" (Bhitarkanika, lCCC, 5,074 t CO2e
issued, 1,200 retired). Editorial layout, Fraunces headline, generous whitespace, satellite before/after
image strip, a "Verify this record" panel showing the report hash, IPFS CID and blockchain
transaction in monospace with a button that recomputes the hash, a ledger-style table of issuance and
retirement events, and methodology/verifier details. Footer with dataset attributions (Copernicus,
Global Mangrove Watch). It should feel like an official public record, trustworthy and calm.
```

### W4 — Retirement certificate (desktop / printable A4)
```
Printable A4 retirement certificate: "Certificate of Retirement — 250 t CO2e" retired by "Konkan Steel
Ltd. (fictional)" on behalf of FY2026-27 emissions, from vintage AQC-BHK-2031-L. Fraunces title, fine
hairline border with subtle tide-line guilloché pattern, project map thumbnail, serial number and hash
in monospace, QR code linking to the public page, verifier and registry signatures area. Elegant,
restrained, no gold gradients.
```

### W5 — Marketplace and forward funding (desktop)
```
Desktop marketplace: left filter rail (state, ecosystem, vintage year, credit type tCCC/lCCC, price
range, verified only). Main list of vintages as dense rows (not cards): mini satellite thumbnail,
project name, state, vintage, available t CO2e, price per tonne in mock USDC, evidence trust
indicator. A secondary tab "Forward funding (reverse auction)" showing an open buyer request:
"5,000 t CO2e, deliver by 2032, max 18 mUSDC per t", with competing project offers sorted by
lowest price and a clear warning line: "Advance funding is not a guarantee of future credits."
```

### M1 — Field agent home (mobile, outdoors)
```
Mobile screen (Android, 390×844) for a field agent: large greeting in English + Hindi, today's tasks
as big tappable rows (Plot P-07 re-measure, Photo point PP-03, Survival count Block B), a prominent
sync status bar ("3 submissions waiting — offline" in turmeric), next low tide time "+0.3 m at 14:20",
and a bottom navigation with 4 large icons. High contrast for bright sunlight, no small grey text.
```

### M2 — Plot capture camera (mobile)
```
Mobile camera capture screen for sample plot P-07: live camera viewfinder with a framing guide for
the plot marker tag, top overlay showing GPS accuracy "±3.2 m (averaging 42 s)" with a progress ring,
"Inside boundary ✓", tide "+0.4 m falling", compass bearing. Big shutter button (72px) at the bottom,
plus a "Can't see marker?" help link. Overlays must stay readable on bright imagery.
```

### M3 — Tree measurement form (mobile)
```
Mobile form for plot P-07 tree records: list of measured trees with species, DBH (cm) and height (m)
in monospace; an "Add tree" flow with a species picker showing common + scientific names (Avicennia
marina — Grey mangrove / तिवर), a large numeric keypad-friendly DBH input with unit, alive/dead toggle,
and a running plot summary (12 trees, est. 26.3 Mg/ha). Works offline; shows "Saved on device".
```

### M4 — Offline sync queue (mobile)
```
Mobile screen showing the offline submission queue: each item with thumbnail, type (photo / plot /
survival count), captured time, size, and state (waiting, uploading 64%, synced, failed with retry).
Clear explanation banner when offline. A large "Sync now" button that is disabled without network.
```

---

## Part 3 — Direction modifiers (append to the master prompt to compare looks)

**A. Estuary (default):** use the master prompt as written.

**B. Field Instrument:**
```
Variation: make it feel like a calibrated scientific instrument. Dark mode by default with background
#0E1615, surfaces #15201E, text #E8E6DF, mangrove accent #5FB38C, tidal accent #5AA9C9. Denser
layouts, more monospace, instrument-style readouts and gauges, thin grid lines. Still no neon, no
gradients.
```

**C. Public Ledger:**
```
Variation: make it feel like an official public ledger and certificate registry. Warmer paper
#F7F2E7, stronger use of Fraunces serif headings, ruled ledger lines in tables, rubber-stamp style
status marks, ink-blue #1E3A5F as primary instead of green, fine engraved patterns used sparingly.
```

---

## Part 4 — How to judge the results
- [ ] Would anyone mistake it for a generic AI/SaaS template? (If yes, reject.)
- [ ] Is the map/satellite image the hero on analytical screens?
- [ ] Are numbers monospaced, aligned, and shown with units and uncertainty?
- [ ] Are status meanings clear without reading (verified / pending / reversal)?
- [ ] Mobile: readable at arm's length in sunlight, 48px+ targets, offline state obvious?
- [ ] Public pages: would a CSR head trust this enough to put the certificate in an annual report?
- [ ] Does it still work with Hindi text (longer strings, Devanagari)?

After choosing: export to Figma and/or HTML, save to `docs/design/exports/<direction>/`, and note the
decision in `docs/CONTEXT.md`. The chosen tokens become the Tailwind theme shared by web and mobile.
