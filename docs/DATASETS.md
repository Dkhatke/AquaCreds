# AquaCreds v2 — Dataset Catalogue

Every dataset the system uses: what it is for, where to get it, its licence, and the caveats. All
downloads are scripted as **DVC stages** (`ml/pipelines/`) so anyone can run `dvc repro` and get the same
data.

> **Why this document exists.** The SIH prototype's main failure was data. It had ~38 hand-typed rows, no
> ground truth for carbon, and lost training images. The fix is to (1) use published, peer-reviewed
> global/regional products as labels and references, (2) validate on held-out geography, and (3) keep
> carbon accounting in a documented engine rather than asking an ML model to "predict credits".

Status column: ✅ verified source/licence on 2026-10-19 · ⚠️ must re-check host/version before download.

---

## 1. Satellite imagery (model inputs)

| Dataset | Use | Resolution / period | Access | Licence | Status |
|---|---|---|---|---|---|
| **Sentinel-2 L2A** (surface reflectance) | Main optical features (bands, indices, composites) | 10–20 m, 5-day revisit, 2017→ | Planetary Computer STAC `sentinel-2-l2a` (COGs; anonymous, optional key for higher limits). Fallbacks: Earth Search `sentinel-2-c1-l2a` (AWS), Copernicus Data Space Ecosystem | Copernicus open data (attribution) | ✅ |
| **Sentinel-1 RTC** (radar backscatter) | Cloud-free structure/inundation features (VV, VH, ratio, temporal std) | 10 m, 6–12 day | Planetary Computer `sentinel-1-rtc`; fallback CDSE GRD + own RTC | Copernicus open data | ⚠️ check PC access terms for RTC |
| **Copernicus DEM GLO-30** | Elevation, slope (mangroves sit near mean sea level) | 30 m | Planetary Computer `cop-dem-glo-30` | Copernicus DEM licence (free) | ✅ |
| **Landsat 8/9 C2 L2** | Historical baselines before 2017 (optional) | 30 m, 2013→ | Planetary Computer `landsat-c2-l2` | Public domain (USGS) | ✅ |

**Preprocessing rules** (see IMPLEMENTATION_PLAN §6.2): SCL cloud/shadow mask; BOA offset −1000 for
processing baseline ≥ 04.00; ÷10000 to reflectance; seasonal medians; ≥ 3 clear observations per pixel.

---

## 2. Ecosystem labels and reference maps (classifier training/validation)

| Dataset | Use | Details | Access | Licence | Status |
|---|---|---|---|---|---|
| **coastTrain v1.0** (Murray et al. 2022, *Remote Sensing* 14:5766) | Labelled points for mangrove, saltmarsh, tidal flat and other coastal types | 193,105 occurrence records, 7 coastal ecosystem types, harmonised to the IUCN Global Ecosystem Typology | Data link in the paper's Data Availability section (DOI 10.3390/rs14225766) | Open (check record) | ⚠️ locate repository record |
| **Global Mangrove Watch v3.0** | Mangrove extent 1996–2020; positive-sample polygons; change baseline | 25 m; 2020 extent ≈147,359 km² globally | Zenodo record 6894273 | CC BY 4.0 | ✅ |
| **Global Mangrove Watch v4.0** (2020 baseline) | Improved 10 m mangrove extent for 2020 | 10 m | GEE Community Catalog / GMW portal (find the official download host) | CC BY 4.0 (per GEE catalog) | ⚠️ confirm host + version |
| **Global tidal marsh 2020** (Worthington et al. 2024, *GEB* 33:e13852) | Salt-marsh positives | 10 m, 60°N–60°S, RF classifier, OA 0.85 | Data availability section of DOI 10.1111/geb.13852 | CC BY | ⚠️ locate download |
| **ESA WorldCover 2020/2021** | Negative classes (water, cropland, built-up, tree cover, bare) | 10 m | Planetary Computer `esa-worldcover` | CC BY 4.0 | ✅ |
| **Murray Global Intertidal Change** (tidal flats) | Tidal-flat class support | 30 m | GEE / project site | Open | ⚠️ optional |
| **Allen Coral Atlas** (benthic: seagrass) | Seagrass research layer (clear reef waters only: Lakshadweep, Andaman, Gulf of Mannar) | 5 m (Planet) | allencoralatlas.org downloads | CC BY 4.0 | ⚠️ optional / research |
| **Manual gold set** (ours) | Unbiased test set for accuracy + Olofsson area CIs | ~500 points, stratified random, interpreted on high-res basemaps | Created in QGIS, stored in DVC | Ours | to build |

**coastTrain findings for the AOI window (68–97.5°E, 6–24.8°N), measured 2026-10-19:**
19,275 usable points after dropping coral reef.

| Label | Field / image-interpreted | Map-derived (sampled from GMW 1996, 25 m) |
|---|---|---|
| mangrove | 934 | 17,169 |
| terrestrial_other | 384 | 0 |
| tidal_flat | 286 | 0 |
| water | 261 | 0 |
| seagrass | 224 | 0 |
| salt_marsh | 17 | 0 |

Consequences:
1. Map-derived mangrove points (reference year 1996) are kept **only where GMW v4 (2020) still
   shows mangrove**, because many 1996 mangroves are now ponds or farmland.
2. Negatives are too scarce, so they are supplemented from **ESA WorldCover** (read via STAC
   for the AOI only).
3. **Salt marsh (17 points) cannot be trained on.** It stays a research class unless the
   Worthington 2024 map is obtained.
4. The 934 field/interpreted mangrove points are prioritised for the **gold/test** split.

Verified Zenodo records: coastTrain v1.0.0 = **7080756** (7.3 MB); GMW v4.0.19 2020 =
**12756047** (180 MB GeoTIFF zip); GMW v3 = **6894273**; Maxwell SOC = **7729491** (4.8 GB of
tiles, deferred: above the 500 MB download guard).

**Label construction:**
1. Merge sources.
2. Erode polygons 30 m before sampling (avoids mixed pixels).
3. Drop points where sources disagree.
4. De-duplicate within 50 m.
5. Cap points per H3 cell (balances the Sundarbans against small sites).
6. Hold out the Gulf of Kutch region entirely as the geographic test set.

---

## 3. Biomass and carbon references (regression training/validation)

| Dataset | Use | Details | Access | Licence | Status |
|---|---|---|---|---|---|
| **GEDI L4A Footprint AGBD v2.1** | Primary AGB training labels | ~25 m footprints, 51.6°N–S (covers India), 2019→ (gap during 2023–24 ISS storage) | NASA ORNL DAAC via `earthaccess` (free Earthdata login) | NASA open data | ⚠️ confirm current version |
| **ESA CCI Biomass** | Secondary AGB reference / sanity check | 100 m, multiple epochs (2010, 2015–2022) | CEDA archive | Open (CCI licence) | ⚠️ confirm latest version |
| **Simard et al. 2019 — Global Mangrove AGB & Canopy Height** | Comparison only (nominal year 2000) | 30 m; includes field plot data | ORNL DAAC ds 1665 (DOI 10.3334/ORNLDAAC/1665), Earthdata login | Open | ✅ |
| **ETH Global Canopy Height 2020** (Lang et al.) | Height feature for AGB model | 10 m | ETH research collection / GEE community catalog | CC BY 4.0 | ⚠️ confirm host |
| **Meta/WRI High-Res Canopy Height** (Tolan et al. 2024) | Optional height feature (aggregate to 10 m) | ~1 m | AWS Open Data | CC BY 4.0 | ⚠️ optional |
| **AlphaEarth Satellite Embedding V1** (Google DeepMind) | Foundation-model features (64-d) for both models | 10 m, annual 2017–2025 | COGs in `gs://alphaearth_foundations` and AWS Source Cooperative mirror; also GEE | CC BY 4.0 (required attribution text) | ✅ |
| **Global Mangrove Soil Organic Carbon 2020** (Maxwell et al. 2023, *Data in Brief* 50:109621) | Soil carbon **context** layer (stock 0–100 cm, t/ha, with prediction intervals) | 30 m; trained on 10,331 soil samples | Zenodo 7729491 (COG mosaics) | CC BY 4.0 | ✅ |
| **Sanderman et al. 2018** mangrove SOC | Comparison (year 2000) | 30 m | Harvard Dataverse | CC BY | optional |
| **Global Wood Density Database** (Zanne et al. 2009) | ρ per species for the Komiyama equations | Species-level | Dryad | CC0 | ⚠️ confirm record |
| **Literature field plots (India)** | **Independent** AGB validation | Compiled table: site, lat/lon, year, plot size, AGB Mg/ha, source DOI | Manual compilation from published studies (Sundarbans, Bhitarkanika, Pichavaram, Godavari, Andaman, Kutch) | Cite each paper | to build |
| **Pilot partner plots** | Calibration + validation (future) | DBH/height/species per tree | Collected via the mobile app's plot form | Partner agreement | future |

**Equations and factors** (implemented in `packages/aqua_carbon`, each with a citation):
- Komiyama et al. 2005 common mangrove allometry: AGB = 0.251·ρ·D^2.46 (✅ confirmed via a secondary
  table, n = 104, R² = 0.98); BGB = 0.199·ρ^0.899·D^2.22 (kg, D in cm, ρ in g/cm³) ⚠️ **BGB coefficients
  still need a full-text check.**
- AR-TOOL14 v04.1 defaults (used by CCTS via BM-T-AR-0004): carbon fraction 0.47; baseline tree
  root-shoot 0.25; shrub root-shoot 0.40; uncertainty-discount table (Appendix 2).
- BM FR05.001 default soil accrual: 0.50 t C/ha/yr for 20 years after planting.
- IPCC 2013 Wetlands Supplement, Chapter 4 (coastal wetlands): Tier-1 defaults for root:shoot, carbon
  fraction, and soil accumulation rates. Encoded as methodology-profile parameters with page references.
- CO₂/C = 44/12.

---

## 4. Field-photo verifier data (Model C)

| Dataset | Use | Access | Licence filter | Status |
|---|---|---|---|---|
| **iNaturalist research-grade observations via GBIF** | Positives: Indian mangrove genera (*Rhizophora, Avicennia, Sonneratia, Bruguiera, Ceriops, Excoecaria, Heritiera, Kandelia, Aegiceras, Lumnitzera, Xylocarpus*). Negatives: other coastal/terrestrial plants | GBIF occurrence download (free account) with `mediaType=StillImage`, filtered to South/Southeast Asia + global | CC0, CC BY, CC BY-NC only; store licence + attribution per image | ✅ |
| **Scene negatives** | Beaches, bare mudflats, water, plantations, urban, indoor, screen-recapture photos | Open Images / Places365 subsets (licence-filtered), plus our own recaptures | Per-source | ⚠️ |
| **BioCLIP 2** pretrained weights | Zero-shot + embedding model | Hugging Face `imageomics/bioclip-2` | MIT | ✅ |
| **DINOv2** weights | Alternative embedding model | Hugging Face `facebook/dinov2-*` | Apache 2.0 | ✅ |
| **Our app captures** | Real field distribution (domain shift check) | Pilot uploads (with consent) | Ours | future |

Target size for v2.0: ~3–5k positives and ~5k negatives, split by **observer and location** (not random)
to avoid leakage.

---

## 5. Context, boundaries and sanity checks

| Dataset | Use | Access |
|---|---|---|
| **India State of Forest Report 2023** (FSI) — mangrove cover by state | Sanity-check mapped areas per state | fsi.nic.in (PDF tables) |
| **Bhuvan / Bhoonidhi (NRSC/ISRO)** | Optional Indian imagery (Resourcesat LISS-IV) and thematic layers; mentioned in the legacy README | bhoonidhi.nrsc.gov.in (registration) |
| **Natural Earth / GADM** | Coastline, state boundaries for the AOI | naturalearthdata.com (public domain) / GADM (non-commercial) |
| **Protected areas (WDPA)** | Flag projects inside protected areas (additionality / permissions) | protectedplanet.net (non-commercial terms) |

---

## 6. Demo / pilot sites

| Site | State | Why |
|---|---|---|
| Bhitarkanika | Odisha | Dense, well-studied mangroves; many literature plots |
| Pichavaram | Tamil Nadu | Restoration history; smaller extent |
| Gulf of Kutch | Gujarat | Large restoration programmes (MISHTI); **held-out ML test region** |
| Sundarbans | West Bengal | Largest Indian mangrove; training-set anchor (capped per H3 cell) |
| Godavari–Krishna delta | Andhra Pradesh | Aquaculture conversion; good negative/change examples |
| Palk Bay / Gulf of Mannar | Tamil Nadu | Seagrass research layer (clear water) |

---

## 7. Accounts needed (all free)

| Account | For |
|---|---|
| NASA Earthdata | GEDI L4A, Simard (ORNL DAAC) |
| Microsoft Planetary Computer (optional API key) | Higher STAC/SAS limits |
| Copernicus Data Space Ecosystem | Fallback imagery |
| GBIF | Image occurrence downloads |
| Hugging Face | Model weights (BioCLIP 2, DINOv2) |
| Google Cloud + Earth Engine (noncommercial, optional) | Research notebooks only |
| Cloudflare | R2 bucket (DVC remote + prod object storage) |
| Kaggle / Google Colab | Free GPU for image models |

---

## 8. Data governance
- Store licence + attribution for every image and dataset in `data/LICENSES.csv`. Attribution is shown on
  the public site where required (AlphaEarth, Copernicus, GMW, CC BY images).
- No CC BY-NC data in anything sold commercially in the future. Track the NC flag per sample so the
  training set can be rebuilt without it.
- Field photos are personal data: private bucket, EXIF stripped and faces blurred in public copies,
  retention policy, consent captured in the app.
