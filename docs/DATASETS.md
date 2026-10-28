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
| **Sentinel-2 L2A** (surface reflectance) | Main optical features (bands, indices, composites) | 10–20 m, 5-day revisit, 2017→ | Planetary Computer STAC `sentinel-2-l2a` (COGs; anonymous, optional key for higher limits). Automatic fallback: Earth Search v1 `sentinel-2-l2a` (AWS public COGs; see §1.1). Manual: Earth Search `sentinel-2-c1-l2a`, Copernicus Data Space Ecosystem | Copernicus open data (attribution) | ✅ |
| **Sentinel-1 RTC** (radar backscatter) | Cloud-free structure/inundation features (VV, VH, ratio, temporal std) | 10 m, 6–12 day | Planetary Computer `sentinel-1-rtc` (see §1.2); no automatic fallback (Earth Search has GRD only; CDSE GRD + own RTC would be a new pipeline) | CC BY 4.0 (Catalyst RTC of Copernicus data) | ✅ 2026-10-20 |
| **Copernicus DEM GLO-30** | Elevation, slope (mangroves sit near mean sea level) | 30 m | Planetary Computer `cop-dem-glo-30` | Copernicus DEM licence (free) | ✅ |
| **Landsat 8/9 C2 L2** | Historical baselines before 2017 (optional) | 30 m, 2013→ | Planetary Computer `landsat-c2-l2` | Public domain (USGS) | ✅ |

**Preprocessing rules** (see IMPLEMENTATION_PLAN §6.2): SCL cloud/shadow mask; BOA offset −1000 for
processing baseline ≥ 04.00 unless the provider already removed it (§1.1); ÷10000 to reflectance;
seasonal medians; ≥ 3 clear observations per pixel.

### 1.1 Sentinel-2 STAC providers and fallback

`GEO_STAC_PROVIDERS` (default `planetary-computer,earth-search`) is the ordered list the worker tries.
A search or read that fails with a timeout, connection error, HTTP 5xx/408/429 or HTTP 401/403 moves
to the next provider; HTTP 400/404 and other errors are raised (they are bugs or configuration errors,
which another catalogue would only hide). Each stored scene records the provider that served it
(`scene_observations.provider`); run outputs list `providers_used`, `search_fallbacks` and
`provider_fallbacks`. Code: `aqua_geo.fallback`, `aqua_geo.sentinel2.PROVIDERS`,
`aqua_worker.scenes.StacSceneSource`.

Conventions, verified on 2026-10-20 against live STAC items over Bhitarkanika (MGRS 45QVC; recorded,
trimmed copies are the test fixtures in `packages/aqua_geo/tests/fixtures/stac/`):

| | Planetary Computer `sentinel-2-l2a` | Earth Search v1 `sentinel-2-l2a` (`earthsearch:` items) | Earth Search `sentinel-2-c1-l2a` |
|---|---|---|---|
| Name in settings | `planetary-computer` | `earth-search` | `earth-search-c1` (not in the default list) |
| Asset keys | `B02` … `B12`, `B8A`, `SCL` | `blue`, `green`, `red`, `rededge1-3`, `nir`, `nir08`, `swir16`, `swir22`, `scl` (plus `*-jp2` duplicates, unused) | same keys as Earth Search `sentinel-2-l2a` |
| Signing | SAS token (`planetary_computer.sign`) right before each read | none (public S3 over HTTPS) | none |
| BOA offset in DNs | present for baseline ≥ 04.00 (PC runs its own L2A; never harmonised) | **removed** when `earthsearch:boa_offset_applied: true` (e.g. every 2024–2026 item checked); **present** when the flag is false (e.g. 2022-02-13, baseline 04.00) | present (all items are baseline ≥ 05.00) |
| `raster:bands` scale/offset | not given | 0.0001 / −0.1 on every item, **even flagged ones**: not reliable, never used | 0.0001 / −0.1, consistent with the GeoTIFF offset tag |
| Item id / `datetime` | `S2B_MSIL2A_20240114T044139_R033_T45QVC_…`; datatake start (04:41:39) | `S2B_45QVC_20240114_0_L2A`; granule sensing time (04:52:56) | `S2B_T45QVC_20240114T044648_L2A` |

Evidence for the offset rule: the same 2024-01-14 T45QVC scene read through a 32×32 window was
exactly 1000 DN lower in Earth Search `sentinel-2-l2a` (flag true; red 943–1616) than in
`sentinel-2-c1-l2a` (1943–2616). Earth Search `sentinel-2-l2a` also lists some scenes twice
(`…_0_L2A` baseline 03.01 and its `…_1_L2A` 05.00 reprocessing, e.g. 2021-12-05): the highest
baseline is kept. Because ids and timestamps differ per provider, the same acquisition is identified
by platform + MGRS tile + UTC date (`aqua_geo.sentinel2.scene_key`) and stored once even if a later
run is served by another provider. Collection 1 is a manual option only: it had no items over
Bhitarkanika for February 2022 when checked.

### 1.2 Sentinel-1 RTC (monsoon gap filling)

- **Source:** Planetary Computer `sentinel-1-rtc` (Catalyst RTC from ESA GRD, PlanetDEM, IW mode),
  assets `vv`/`vh` = terrain-corrected γ⁰ in **linear** power, float32 COGs at 10 m in UTM, nodata
  −32768. Over India the passes are VV+VH dual-pol (S1A and S1C).
- **Licence:** CC BY 4.0. Attribution: "Contains modified Copernicus Sentinel data, processed by
  Catalyst; hosted by Microsoft Planetary Computer".
- **Access terms:** the collection is flagged `msft:requires_account: true` and its description says
  "A Planetary Computer account is required to retrieve SAS tokens to read the RTC data". On
  2026-10-20 an anonymous token still allowed a ranged read (HTTP 206) and the opt-in smoke test
  passed without a key, but that is not guaranteed: set `PC_SDK_SUBSCRIPTION_KEY` (free PC account)
  in production. A refused token surfaces as an HTTP 401/403 error recorded in
  `outputs.sentinel1.error` / `failed`, never as missing data presented as valid.
- **When it is ingested** (`GEO_S1_*` settings): acquisitions in the monsoon months
  (`GEO_S1_MONSOON_MONTHS`, default 6,7,8,9), and acquisitions within ±`GEO_S1_WINDOW_DAYS` (6) of a
  stored Sentinel-2 scene whose polygon clear fraction is below `GEO_S1_CLEAR_FLOOR` (0.3).
  `GEO_S1_ENABLED=false` turns it off.
- **Per acquisition** (one pass = every slice of one absolute orbit): VV and VH clipped to the polygon
  in UTM at 10 m; 3×3 NaN-aware median on linear γ⁰ against speckle (single passes have only ~4.4
  equivalent looks); then VV/VH in dB, VH/VV (linear and dB), and RVI = 4·VH/(VV+VH); per-polygon
  mean/P10/P50/P90 and valid fraction. The P50 (a per-polygon median) is the headline value; temporal
  medians belong to seasonal composites. Code: `aqua_geo.sentinel1`.
- **Storage:** no table yet. Results go to `analysis_runs.outputs.sentinel1.observations` (one entry
  per pass with its STAC item ids, orbit, trigger and statistics); later runs skip passes already
  recorded there. A dedicated `sar_observations` table is a pending migration.

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

**Built training labels (`ml/pipelines/labels_build.py`, run 2026-10-19, 48 s, 0.62 GB RAM):**
14,129 points: mangrove 4,801 · water 4,763 · terrestrial_other 2,662 · cropland_aquaculture 1,396
· tidal_flat 282 · built_bare 210 · salt_marsh 15 (not trained in v2.0). Only 55 of the 17,169
1996-sampled mangrove points were absent from GMW 2020. That is far fewer than expected loss, and
likely because coastTrain sampled stable mangrove cores; to be spot-checked on imagery. Splits:
5 spatial folds of ~2,600 points each + 1,133 held out (Gulf of Kutch). WorldCover negatives
are read as full-resolution windows per H3 cell (100% agreement with per-point reads), with
Planetary Computer URLs re-signed per tile (tokens expire after ~45 min).

**Label construction:**
1. Merge sources.
2. Erode polygons 30 m before sampling (avoids mixed pixels).
3. Drop points where sources disagree.
4. De-duplicate within 50 m.
5. Cap points per H3 cell (balances the Sundarbans against small sites).
6. Hold out the Gulf of Kutch region entirely as the geographic test set.

**Classifier features (`ml/pipelines/features_extract.py`, run 2026-10-19/20):** Sentinel-2
L2A, dry season Nov 2020 – Mar 2021, read at every label point (salt_marsh excluded) with the
Planetary Computer data-API point endpoint (server-side COG sampling). Window reads were not
viable: this machine measured 0.3–1 MB/s from the COG stores, and the labels touch ~3,600
distinct 10 m blocks (~90 GB for 12 scenes). Per point: the 12 least-cloudy scenes whose
footprint covers it (one per date), DNs converted with `aqua_geo.sentinel2.scale_and_mask` and
aggregated with `aqua_geo.features.pixel_features` (production code), plus Copernicus DEM
GLO-30 elevation. Result: **14,114 / 14,114 points, 813 / 813 H3 cells, 0 failed cells, 0 failed
requests** (183,482 point requests, 105 transient retries). Every point has ≥ 3 clear
observations (median 12, p10 11, minimum scenes 5), so none was dropped. Class balance is
unchanged from the labels: mangrove 4,801 · water 4,763 · terrestrial_other 2,662 ·
cropland_aquaculture 1,396 · tidal_flat 282 · built_bare 210. Elevation is missing for 8
offshore water points (no DEM tile over open sea), filled with 0 m at training. Runtime: the
active request time was ~2.5–3 h at ~19 req/s (client-capped at 20 req/s, after Planetary
Computer rate-limited the run at ~38 req/s); the wall time was 9.6 h because the laptop slept
mid-run. Peak RSS 261 MB. 20 m bands are sampled at their native pixel (production resamples
them bilinearly to 10 m). Sentinel-1 was not extracted in v1 (another ~100k requests).

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
| **Global Wood Density Database** (Zanne et al. 2009) | ρ per species for the Komiyama equations | Species-level | Dryad doi:10.5061/dryad.234 (see §3a) | CC0 1.0 | ✅ |
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

### 3a. Wood density reference (`data/reference/wood_density_mangroves.csv`)

- **Source:** Global Wood Density Database v1, Zanne et al. 2009, Dryad doi:10.5061/dryad.234
  (data paper Chave et al. 2009, *Ecol. Lett.* 12:351–366). **Licence CC0 1.0** (Dryad record,
  checked 2026-10-19).
- **Retrieval:** Dryad's file download now needs an API token, so the 16,467 GWDD records were read
  from the verbatim copy shipped as `wdData` in CRAN BIOMASS 2.2.7-1 (cites the same Dryad source).
  Retrieval date, DOI and licence are stored on every row; the rebuild script is
  `data/reference/build_wood_density.py`.
- **Coverage:** species-level means (with n, SD, min/max, regions, GWDD reference numbers) for all
  18 target Indian species (*Avicennia marina, A. officinalis, A. alba, Rhizophora mucronata,
  R. apiculata, Bruguiera gymnorhiza, B. cylindrica, Ceriops tagal, C. decandra, Sonneratia
  apetala, S. alba, S. caseolaris, Excoecaria agallocha, Heritiera fomes, Kandelia candel,
  Aegiceras corniculatum, Lumnitzera racemosa, Xylocarpus granatum*) plus 7 more Indian species and
  genus means for the 11 genera. n ranges from 1 to 6 per species.
- **Fallbacks:** a species without a row gets its genus mean, flagged `genus_fallback=true` and
  reported as a warning in the MRV record; an unknown genus is an error (never a guess).
- **Caveat:** almost all records are from South-East Asia or Australia/PNG (only 3 Indian records).
  Replace with regional or site-measured densities when available. GWDD v2 (Zenodo
  doi:10.5281/zenodo.18262736, CC BY 4.0) supersedes v1 and is a candidate upgrade.

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
| Microsoft Planetary Computer (API key; optional for Sentinel-2, required by the terms for `sentinel-1-rtc`) | Higher STAC/SAS limits; Sentinel-1 RTC |
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

---

## 9. DVC remote (where the data and model artifacts live)

Every DVC output (raw downloads, labels, features, model artifacts) must exist outside any single
clone: the classifier v1 artifacts were lost once because their only copy sat in a clone under
`/tmp`, which macOS cleans automatically. Push after every stage that produces a new output.

**Stopgap (now): a local remote per machine.** It lives in `.dvc/config.local`, which is
machine-specific and git-ignored, so the committed `.dvc/config` stays empty:

```
mkdir -p ~/Documents/aquacreds-dvc-store
uv run dvc remote add --local -d localstore ~/Documents/aquacreds-dvc-store
uv run dvc push                 # after each `dvc repro`
uv run dvc pull                 # in a fresh clone on the same machine
```

Keep the store outside `/tmp` and outside any clone. A clone made on the same machine only needs
the same two `dvc remote add` lines before `dvc pull`. Back it up if the machine is the only copy.

**Target: Cloudflare R2 (decision D9).** When the R2 keys exist, add the bucket as the shared,
committed default remote and copy the local store into it once:

```
# committed (.dvc/config): no secrets, only the bucket and endpoint
uv run dvc remote add -d r2 s3://<bucket>/dvc
uv run dvc remote modify r2 endpointurl https://<account-id>.r2.cloudflarestorage.com
uv run dvc remote modify r2 region auto
# secrets stay local (.dvc/config.local), never committed
uv run dvc remote modify --local r2 access_key_id <R2 access key id>
uv run dvc remote modify --local r2 secret_access_key <R2 secret access key>
# make R2 the default here and stop using the local store
uv run dvc remote default --local r2        # or: dvc remote remove --local localstore
uv run dvc push -r r2                        # uploads everything in the local cache
```

`dvc[s3]` is already in the `ml` dependency group. `dvc push -r r2` uploads from the local cache, so
run `dvc pull -r localstore` first on a clone whose cache is empty. Afterwards, `dvc status -c -r r2`
must report nothing missing. The model files the worker serves are a separate copy in object
storage, pinned by SHA-256 in `services/worker/src/aqua_worker/model_store.py`
(`python -m aqua_worker.model_store publish <artifact dir>`).
