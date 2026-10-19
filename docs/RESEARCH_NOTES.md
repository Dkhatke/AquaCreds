# AquaCreds v2 — Research Notes and Plan Re-verification

Research date: **2026-10-19**. This document answers the open project questions with sources and lists
the changes they force on the implementation plan (§6).

Primary sources read in full:
- **BM FR05.001 v1.0** (BEE, 27 Mar 2025), the India CCTS methodology for afforestation/reforestation of
  degraded mangrove habitats.
- **CDM AR-TOOL14 v04.1**, the tree/shrub carbon-stock tool. India's **BM-T-AR-0004** is its CCTS
  adoption, referenced by BM FR05.001.
- **MISHTI Operational Guidelines** (MoEFCC / National CAMPA).

---

## Q1. Mangroves grow on intertidal land with no normal land registry. How do we prove the right to run a project, and what does the photo/EXIF check actually prove?

### What the research shows
1. **The methodology itself does not ask for a land title.** BM FR05.001 defines eligibility by
   *ecology* only: "degraded mangrove habitat", meaning wetland where mangroves can grow, with
   waterlogged saline/brackish sediment and forest cover reduced below the host-country level. It also
   requires >90% of the area planted with mangrove species and soil disturbance ≤10%. Legal right to
   implement is handled by the CCTS **project registration process** (Detailed Procedure for the Offset
   Mechanism + ACVA validation), not by the methodology. ⚠️ The exact document checklist in the Detailed
   Procedure still needs to be read; it was not retrievable in this session.
2. **Mangrove land in India is mostly government-controlled, under several regimes, not unowned:**
   - **State Forest Departments** are the legal owners/managers of most forest land, including many
     mangroves notified as Reserved/Protected Forest. MISHTI puts "overall responsibility for
     implementation … with the State Forest Department" and covers both **forest and non-forest land**.
   - **Revenue (government) land:** intertidal mudflats, accreted land, abandoned aquaculture ponds and
     salt pans. MISHTI explicitly targets "mudflat areas", "abandoned aquaculture ponds & salt pans" and
     "accretion areas".
   - **Private land** exists too. In Kerala, at least half of mangrove areas are reported as privately
     owned.
   - **Regulation:** mangroves are **CRZ-IA** (ecologically sensitive) under the CRZ Notification 2019,
     and the intertidal zone between the High and Low Tide Lines is **CRZ-IB**. Permissions run through
     the State Coastal Zone Management Authority (CZMA).
3. **How real projects handle it:** through agreements, not title deeds.
   - In a corporate-backed Maharashtra project, the village signed a **conservation agreement** under
     which land ownership remained unchanged.
   - Delta Blue Carbon (Pakistan, Verra VM0033) is a joint venture with the provincial government and
     uses **Mangrove Stewardship Agreements** with communities.
   - Commentators flag carbon-rights ambiguity and benefit-sharing as India's main barrier for AFOLU
     projects.

### How AquaCreds v2 solves it: a "tenure & authorisation evidence pack" instead of a land-registry lookup
The PPT's "Survey number ownership reports, govt land registry check" is replaced by:

| Tenure type (enum) | Evidence required (uploaded, hashed, verifier-checked) |
|---|---|
| `forest_land` | Agreement/MoU or work order from the State Forest Department (e.g. JFM/eco-development committee arrangement, MISHTI/CAMPA convergence letter) |
| `revenue_government_land` | District Collector / Revenue Department permission or NOC; land category extract where one exists |
| `port_or_central_land` | Port Trust / central agency lease or permission (e.g. salt-pan land) |
| `community_panchayat` | Gram Sabha/Panchayat resolution + **FPIC** (free, prior, informed consent) record + benefit-sharing agreement |
| `private_land` | Title documents (e.g. 7/12 extract, patta) + owner consent + carbon-rights assignment |
| **All types** | CRZ/CZMA clearance or a written statement that plantation is a permitted activity; **carbon-rights and benefit-sharing agreement** naming who receives credits/revenue |

**Platform checks:**
- Boundary overlap against other AquaCreds projects (PostGIS) to prevent double counting.
- Overlap flags against public layers: WDPA protected areas, and state CZMP/CRZ maps where available
  (often PDFs only, digitised manually per pilot site).
- **Ecological eligibility test** from satellite: a historical mangrove presence/loss signal from GMW
  1996–2020 plus current non-mangrove cover. This supports the "degraded mangrove habitat" condition.
- The verifier signs a tenure checklist. All documents are stored privately, and only their hashes go
  on-chain.

**We do not claim to resolve legal ownership.** We make the authorisation chain explicit, documented and
auditable. The UI states: "Tenure evidence reviewed by verifier; not a legal title determination."

### What the geotagged photo and EXIF check actually proves
A photo **cannot** prove ownership. It proves **presence, timing and site condition**, and supports the
inventory:

| The photo proves | It does not prove |
|---|---|
| Someone with the app was at (approximately) this location at this time (nonce + server time + GPS) | Who owns the land |
| What the vegetation looked like (survival, health, species via the photo model) | Carbon stock (that comes from plots + allometry) |
| A specific **sample plot** was visited and measured (plot-centre photo with tag/stake) | That the GPS is precise to the metre |

Practical constraints and how we handle them:
- **GPS under dense canopy:** smartphone GNSS error can reach several metres (≈1.5 m for good dual-band
  phones, much worse for older ones and in dense canopy; 20–30 m reported for handheld units under
  canopy). The app records reported accuracy, averages fixes over 30–60 s at plot centres, and the
  geofence uses a buffer equal to the accuracy. **Permanent plots are physically marked** (PVC pipe/tag
  ID visible in the photo) so re-measurement doesn't depend on GPS alone.
- **Tides:** the intertidal zone may be underwater at capture time. The app records predicted tide
  height at capture time (tide tables per site) and guides crews to low-tide windows. Verifiers see the
  tide state next to each photo.
- **Fixed photo-monitoring points:** the same points are re-photographed each period at the same bearing.
  This is the "participatory photo-mapping" that AR-TOOL14 ¶26 explicitly accepts as evidence that crown
  cover has **not decreased** since the last verification.

---

## Q2. Sentinel-2 revisit, time-series processing, and "it will take days"

### Facts
- **Sentinel-2:** the constellation revisit is **5 days** at the equator (one satellite = 10 days).
  Sentinel-2C replaced 2A in operations on 21 Jan 2025, and 2A continues in an extended campaign that
  adds extra observations for some areas. The "14 days" figure doesn't apply.
- **Sentinel-1 (radar, works through cloud):** S1C (launched Dec 2024) is operational and **S1D launched
  4 Nov 2025**, restoring a **6-day** two-satellite revisit.
- **Monsoon:** Indian coasts are heavily clouded June–September, so optical scenes are scarce. SAR works
  regardless. A 2026 Sundarbans study reached ~91% overall accuracy with Sentinel-1 alone and ~96% when
  combined with optical indices.
- Data appears in the catalogues roughly within a day of acquisition. **No MRV process needs real
  time.** Credits are issued per *verification period* measured in years (see Q3).

### Why it will not take days to process
1. **We never download whole tiles.** Sentinel data on Planetary Computer / Earth Search are
   Cloud-Optimised GeoTIFFs, so we read **only the pixels inside the project polygon** (HTTP range
   requests). A 5,000 ha project is ≈500,000 pixels per band, a few MB per scene.
2. **Incremental processing:**
   - **Once, at registration:** backfill 2017→today (~500–700 S2 scenes + S1). This takes tens of
     minutes on the free VM and runs as a background job.
   - **Nightly (Celery beat):** for each active project, search STAC for scenes acquired since the last
     run, process only those, and append per-scene **zonal statistics** (cloud-free fraction, mean
     NDVI/MVI, SAR backscatter, mangrove-probability area) to a Postgres time-series table. Each new
     scene takes seconds.
   - **Seasonally (dry season, Nov–Mar) and annually:** build composites, then run the classifier and
     the biomass model once per composite.
3. **Event-driven alerts:** a sudden NDVI/SAR drop below the project's own historical envelope triggers
   a **loss/degradation alert** to the org and verifier (possible cutting, dieback, or erosion).
4. **Caching:** composites and model outputs are stored as COGs, so reports and maps never recompute.

So the time series is built **continuously and cheaply**. The heavy science runs only a few times a year.

### Satellite limits we must be honest about
- **Young plantations are invisible at first.** Sentinel-2's 10 m pixels can't resolve saplings. Signal
  appears only as canopy closes, typically several years after planting depending on species, density
  and survival. Fast pioneers like *Avicennia* and *Sonneratia* build stock faster than *Rhizophora* in
  the first decade. In early years, **field plots and survival counts carry the evidence**, and the
  satellite mainly checks site condition and catches losses.
- Tidal stage changes the spectral signal of mangroves vs. mudflat vs. water. This is mitigated with
  dry-season composites and SAR temporal statistics.

---

## Q3. How long must a project be monitored, and when are credits released?

### What the methodology says
- **Credits are ex-post only.** BM FR05.001 issues Carbon Credit Certificates for removals measured over
  a **verification period T = t2 − t1**. Nothing is issued on projected growth.
- **Two credit types (not a buffer pool):**
  - **tCCC (temporary):** removals since project start; expire at the end of the next commitment period.
  - **lCCC (long-term):** removals during each verification period; expire at the end of the crediting
    period. **If a later verification shows a loss (lCCC < 0), that many lCCCs must be replaced.**
    This is how CCTS handles reversals.
- **Monitoring:** carbon-stock parameters are measured "at every verification" (AR-TOOL14 data tables),
  following forest-inventory SOPs. Data must be archived for **at least 2 years after the end of the
  last crediting period**.
- **CCTS project start date must be on/after 1 Jan 2025.**
- **Soil carbon default:** dSOC = **0.50 t C/ha/yr for 20 years** after planting, then 0, unless
  site-specific data justifies otherwise.

### Recommended cadence for AquaCreds
| Activity | Frequency | Purpose |
|---|---|---|
| Satellite ingestion + alerts | Nightly (automatic) | Time series, early loss detection |
| Photo-point visits + survival counts | Every 6 months (post-monsoon + dry season) | Survival, condition, fraud deterrence |
| Internal monitoring report (satellite composites + photos) | Annually | Stewardship score, forward-funding milestones |
| **Full plot inventory + verification → credit issuance** | **First at year 3–5** (once DBH is measurable and canopy is closing), then **every 2–5 years** (configurable per project) | lCCC issuance per verification period |
| Crediting period | Per the CCTS Detailed Procedure (CDM A/R convention: 20 years renewable twice, or 30 years fixed) ⚠️ confirm | Long-term permanence |

**Reality check on timing:** a project planted in 2026 would realistically see its **first credits around
2029–2031**. This is exactly why the **reverse-auction forward funding** feature matters: it bridges the
years before first issuance, but it must never be presented as guaranteed credits.

---

## Q4. How are carbon credits calculated, and what are the authoritative resources?

### The calculation chain (BM FR05.001 + AR-TOOL14)

```
Per verification period (t1 → t2):

1. Tree biomass from sample plots (AR-TOOL14):
   per tree: AGB = allometry(DBH, [height], wood density ρ)        e.g. Komiyama 2005 common eq.
             BGB = allometry or AGB × root-shoot ratio
   per plot: Σ trees / plot area → t d.m./ha ; stratum mean ± uncertainty (90% CI)

2. Uncertainty discount (AR-TOOL14 Appendix 2) applied to the mean:
   U ≤ 10% → 0% · 10–15% → 25% of U · 15–20% → 50% · 20–30% → 75% · >30% → 100%
   (project stocks are discounted DOWN, baseline stocks UP: always conservative)

3. Carbon: C = biomass × CF        CF default = 0.47 t C / t d.m.
   CO2e = C × 44/12

4. ΔC_TREE_PROJ = (stock_t2 − stock_t1) × area per stratum
   (+ ΔC_SHRUB, + ΔC_DW optional, + ΔSOC = 44/12 × Σ A_PLANT × 0.50 t C/ha/yr)

5. ΔC_ACTUAL = ΔC_P − GHG_E (non-CO2 from biomass burning; usually 0)
6. ΔC_BSL    = baseline removals (often ≈0 on degraded mudflat; from BM-T-AR-0004)
7. LK        = leakage from displaced agriculture (BM-T-AR-0005; often 0)
8. ΔC_AR     = ΔC_ACTUAL − ΔC_BSL − LK          → net anthropogenic removals
9. lCCC(t2)  = Σ ΔC_AR over (t1, t2] ; tCCC(t2) = Σ ΔC_AR since project start
```

### Worked example (hypothetical numbers, for illustration only)
100 ha planted in 2026 on degraded mudflat (baseline ≈ 0, no leakage). First verification in 2031:
- Plot inventory: mean tree biomass (AGB+BGB) = 25.0 t d.m./ha, uncertainty U = 14% → discount 25% ×
  3.5 = 0.875 → **24.125 t d.m./ha**.
- Tree carbon: 24.125 × 0.47 = 11.34 t C/ha → × 44/12 = **41.58 t CO₂e/ha** → × 100 ha =
  **4,157.5 t CO₂e**.
- Soil (default): 0.50 × 100 ha × 5 yr × 44/12 = **916.7 t CO₂e**.
- ΔC_AR = 4,157.5 + 916.7 − 0 − 0 = **5,074 t CO₂e**, so **lCCC ≈ 5,074** for 2026–2031 (≈10 t
  CO₂e/ha/yr).

For comparison, Delta Blue Carbon's projections work out to roughly 6–11 t CO₂e/ha/yr over 60 years,
depending on the area denominator (our derivation from published totals, not a verified rate).

### Where ML and satellites fit within the rules (important correction to v1 of the plan)
AR-TOOL14 says ex-post tree biomass comes from **sample-plot measurements**, and "remote sensing data may
also be used **in conjunction with**" plots. **A satellite-only biomass model cannot by itself be the
basis of credits under CCTS.** The ML models are still essential, in these legitimate roles:
1. **Stratification:** the methodology recommends stratifying by vegetation type/crown cover. The
   classifier and AGB maps define strata, which reduces the number of plots needed.
2. **Model-assisted / double-sampling estimation:** satellite AGB acts as an auxiliary variable
   regressed against plot measurements (AR-TOOL14 includes double sampling). This tightens the
   uncertainty, which directly reduces the uncertainty discount, so more credits are kept.
3. **Planted area and survival mapping** (area per stratum is a multiplier in every equation).
4. **"No decrease" evidence** (AR-TOOL14 ¶26) via remote-sensing crown-cover checks plus photo points
   between full inventories.
5. **Eligibility evidence** ("degraded mangrove habitat": historical loss from GMW 1996–2020).
6. **Fraud and consistency checks**, plus loss alerts.

### Authoritative resources (to implement from)
| Resource | What it gives us |
|---|---|
| BEE **BM FR05.001** v1.0 (beeindia.gov.in) | The equations above, pools, SOC default, tCCC/lCCC |
| BEE **BM-T-AR-0001…0005** tools (CCTS adoptions of CDM A/R tools) | Additionality/baseline, tree/shrub stocks, dead wood, non-CO₂, leakage |
| **CDM AR-AM0014 v3.0** + **AR-TOOL14 v04.1** (cdm.unfccc.int) | Parent methodology; plot design, sample size, uncertainty discount, defaults (CF 0.47, baseline root-shoot 0.25, shrub root-shoot 0.40) |
| **Verra VM0033 v2.1** | Voluntary-market alternative (tidal wetland & seagrass); a VM0033 profile is kept |
| **IPCC 2013 Wetlands Supplement** (Ch. 4) + **2019 Refinement** (cited by BM FR05.001) | Tier-1 defaults for coastal wetlands |
| **Howard et al. 2014, Coastal Blue Carbon manual** (Blue Carbon Initiative, CI/IOC-UNESCO/IUCN) | Field and lab protocols for mangrove, salt marsh and seagrass carbon |
| **Kauffman & Donato 2012, CIFOR protocols** (doi:10.17528/cifor/003749) | Mangrove plot design, measurement and carbon calculation SOPs |
| **Komiyama et al. 2005**, J. Trop. Ecol. 21:471–477 (doi:10.1017/S0266467405002476) and **Komiyama et al. 2008** review | Common mangrove allometry; AGB = 0.251·ρ·D^2.46 (confirmed via a secondary table; the BGB form still needs a full-text check) |
| **Global Wood Density Database** (Zanne et al.) | ρ by species |
| Indian plantation studies, e.g. Vellar–Coleroon age series (*Avicennia* sequestration rate ~75% higher than *Rhizophora*) and 25-year plantation stocks | Realistic growth expectations and validation |

---

## 5. Python environment: is a virtual environment the right choice?
Yes, with this split:
- **Local development and ML:** one project-level **virtual environment managed by `uv`**
  (`.venv/`, Python 3.12). It isolates the project from macOS's system Python 3.9, installs ~10× faster
  than pip, and `uv.lock` makes it reproducible.
- **Running services (API, workers, DB):** **Docker** containers, each with its own minimal
  dependencies, which is what gets deployed. The venv is for your editor, tests and notebooks.
- **Why not conda?** conda was historically needed for GDAL. Today `rasterio`, `pyogrio` and `shapely`
  ship wheels with GDAL/GEOS bundled, so a plain venv works. conda adds a second package manager and
  slower solves.
- **Why not install globally?** That breaks system tools and makes versions unreproducible across team
  members.

---

## 6. Changes to the implementation plan forced by this research

| # | Change | Plan section affected |
|---|---|---|
| R1 | **Field sample-plot inventory becomes mandatory for crediting**: plot design, sample-size calculator for ±10% at 90% CI, permanent plot markers, re-measurement workflow | Phase 3 (mobile), Phase 5 (engine) |
| R2 | ML reframed: stratification + model-assisted (double-sampling) estimation + area/survival + no-decrease checks + fraud. **Satellite AGB never directly creates credits** | §6, Phase 1e, Phase 4 |
| R3 | Carbon engine implements BM FR05.001 Eq. 1–8, the AR-TOOL14 uncertainty-discount table, CF 0.47, defaults from tools, SOC default 0.50 t C/ha/yr × 20 yr | Phase 5, §6.4–6.5 |
| R4 | Credit model = **tCCC + lCCC** with expiry and **replacement on reversal** (lCCC < 0), not a buffer-pool deduction; ERC-1155 token IDs per vintage × type | Phase 6 contracts |
| R5 | **Tenure & authorisation evidence pack** (types, documents, FPIC, benefit-sharing, CRZ) replaces the land-registry lookup; GMW-based "degraded mangrove habitat" eligibility test | Phase 2 (domain), Phase 5 (review) |
| R6 | Monitoring cadence: nightly ingestion and alerts; 6-monthly photo points; annual internal report; verification and issuance every 2–5 years (first at year 3–5) | Phase 4, Phase 5 |
| R7 | Photo evidence redefined: plot-centre photos with marker IDs, fixed photo points, GPS averaging, tide-state metadata | Phase 3 |
| R8 | CCTS constraints encoded as validation: project start ≥ 2025-01-01, >90% mangrove species, ≤10% soil disturbance (declared and evidenced) | Phase 2 |
| R9 | Data retention: archive monitoring data ≥ 2 years after the last crediting period (backups and object-storage lifecycle) | Phase 9 |

### Still to verify (added to CONTEXT.md)
- CCTS **Detailed Procedure** v1: registration document checklist (tenure proof), crediting-period
  length, verification frequency rules.
- **BM-T-AR-0004** text (assumed identical to AR-TOOL14 v04.1; the BEE site blocked automated
  download).
- Komiyama 2005 **BGB** equation coefficients (full text).
- Mangrove-specific root-shoot ratios for project trees (AR-AM0014 / IPCC Wetlands Supplement).

---

## Sources
- BEE, BM FR05.001 v1.0: https://beeindia.gov.in/sites/default/files/BM%20FR05.001.pdf
- CDM AR-TOOL14 v04.1: https://cdm.unfccc.int/methodologies/ARmethodologies/tools/ar-am-tool-14-v4.1.pdf
- CDM AR-AM0014: https://cdm.unfccc.int/methodologies/DB/KMH6O8T6RL3P5XKNBQE2N359QG7KOE/
- MISHTI Operational Guidelines: https://nationalcampa.nic.in/dashboard/GuidelinesPDF/665052fa8dd59.pdf
- CRZ Notification 2019: https://crz.elaw.in/crz2019.html
- Kerala private mangroves (Mongabay): https://india.mongabay.com/2021/04/kochis-disappearing-mangroves-lock-away-significant-carbon/
- Forest carbon finance & land rights (Mongabay): https://india.mongabay.com/2021/07/study-draws-attention-to-political-reforms-for-the-success-of-forest-carbon-finance/
- ORF, blue carbon credits in India: https://www.orfonline.org/research/overlooked-and-underutilised-blue-carbon-credits-in-india-s-voluntary-carbon-market
- Delta Blue Carbon (Verra): https://verra.org/programs/ccbs/delta-blue-carbon-1-indus-delta-mangrove-restoration-project-phase-1/
- Sentinel-2 mission (SentiWiki): https://sentiwiki.copernicus.eu/web/s2-mission
- Sentinel-1 (Wikipedia): https://en.wikipedia.org/wiki/Sentinel-1
- Sundarbans S1/S2 classification (ISPRS 2026): https://isprs-archives.copernicus.org/articles/XLIX-B3-2026/923/2026/
- Rhizophora vs Avicennia sequestration (SE India): https://link.springer.com/article/10.1007/s11852-013-0236-5
- Planted mangroves store up to 75% of mature stocks (Science Advances): https://www.science.org/doi/10.1126/sciadv.adk5430
- Smartphone GNSS under canopy: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8838512/
- Coastal Blue Carbon manual: https://thebluecarboninitiative.org/manual
- Kauffman & Donato 2012 (CIFOR): https://cifor-icraf.org/knowledge/publication/3749
- Komiyama et al. 2005: https://www.cambridge.org/core/journals/journal-of-tropical-ecology/article/abs/common-allometric-equations-for-estimating-the-tree-weight-of-mangroves/6067C26CECE5B0EF18A319B8DB89B771
- Komiyama 0.251 coefficient (secondary table): https://doi.org/10.3390/f13020325
