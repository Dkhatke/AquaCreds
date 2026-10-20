# Model card: `ecosystem_classifier_v1`

Pixel-level coastal ecosystem classifier on Sentinel-2 dry-season features (Phase 1d).
**Chosen model: LightGBM** (exported to ONNX). All numbers below come from
`ml/model_cards/ecosystem_classifier_v1_metrics.json`, which `dvc repro train_classifier` writes.
Nothing has been rounded up or selected after the fact. The Random Forest baseline is reported in
full.

## Intended use
- **Use it for:** stratification of project areas, ecosystem area mapping (with Olofsson
  error-adjusted areas from a probability sample), change and loss alerts, and consistency checks
  against field evidence.
- **Do not use it for:** direct credit issuance. Credits come from field plots and the
  deterministic `aqua_carbon` engine (decisions D6 and D13). The model is also not validated for
  any year other than 2020–21, for monsoon-season imagery, or for salt marsh or seagrass.

## Data
- **Labels:** `data/processed/labels.parquet`, built by `labels_build.py`. The sources are
  coastTrain v1.0 points, re-checked against GMW v4 2020, plus ESA WorldCover 2021 negatives.
  Salt marsh (15 points) is excluded. That leaves 14,114 points in 6 classes and 813 H3 res-5
  cells.

  | class | folds 0–4 | Gulf of Kutch (held out) |
  |---|---:|---:|
  | mangrove | 4,678 | 123 |
  | water | 4,076 | 687 |
  | terrestrial_other | 2,565 | 97 |
  | cropland_aquaculture | 1,280 | 116 |
  | tidal_flat | 225 | 57 |
  | built_bare | 157 | 53 |

- **Features** (30): one feature vector per point, produced by `features_extract.py` with the
  production code `aqua_geo.features.pixel_features`:
  - Sentinel-2 L2A for Nov 2020 – Mar 2021. Each point uses up to 12 least-cloudy scenes covering
    it, SCL-masked, with the BOA offset rule applied.
  - The median of the 10 bands.
  - The median of 9 indices, each computed per scene: NDVI, EVI, SAVI, NDWI (McFeeters), MNDWI,
    NDMI, MVI, CMRI and NDRE.
  - The temporal p10/p50/p90 of NDVI, MVI and NDWI.
  - Copernicus DEM GLO-30 elevation. It is missing for 8 offshore water points (no DEM tile) and
    is filled with 0 m. Production must apply the same fill (`features.json: fill_values`).
  - `clear_count` is not used as an input, because it describes cloudiness, not the surface.

  Every point had at least 3 clear observations (median 12). No extraction failed: 813/813 cells
  and 0/183,482 requests.

## Validation design
- **Spatial 5-fold CV:** fold_0..fold_4 are whole H3 res-5 cells (~250 km²), so no cell is
  ever split between training and validation.
- **Held-out region:** the Gulf of Kutch (68.0–70.8°E, 22–24°N), an arid coast that is unlike
  most of the training data. It was never used for training, tuning or model selection. Each
  final model is refit on all 5 folds and scored on it once.
- **Tuning:** Optuna TPE, 30 trials per model, maximising the mean CV macro-F1. Because the same
  folds are used for tuning and for reporting, the **CV scores are slightly optimistic**. The
  held-out region is the clean estimate.
- **Class imbalance:** the classes range from 157 to 4,678 training points. RF and LightGBM use
  `class_weight="balanced"`. XGBoost uses balanced `sample_weight` (n / (k · n_class)). No
  resampling was used. Macro-F1 is the headline metric, so the small classes count equally.
- **Selection rule (fixed before training):** highest held-out macro-F1, then CV macro-F1, both
  rounded to 0.01, because the sampling error with ~1,100 held-out points is about ±0.01–0.02.
  The simpler model wins any remaining tie.

## Results

| model | CV macro-F1 (mean ± sd) | CV accuracy | CV mangrove F1 | **Kutch macro-F1** | Kutch accuracy | Kutch mangrove P / R / F1 | Kutch Brier / ECE |
|---|---|---|---|---|---|---|---|
| Random Forest (baseline) | 0.751 ± 0.023 | 0.912 ± 0.016 | 0.971 ± 0.007 | **0.636** | 0.754 | 1.000 / 0.911 / 0.953 | 0.318 / 0.045 |
| **LightGBM (chosen)** | 0.760 ± 0.025 | 0.913 ± 0.013 | 0.973 ± 0.006 | **0.638** | 0.755 | 0.982 / 0.911 / 0.945 | 0.314 / 0.081 |
| XGBoost | 0.757 ± 0.022 | 0.916 ± 0.013 | 0.973 ± 0.004 | **0.640** | 0.775 | 0.983 / 0.943 / 0.963 | 0.303 / 0.080 |
| majority class (reference) | — | — | — | 0.033 | 0.109 | — | — |

Per-fold CV macro-F1 values:

| model | fold 0 | fold 1 | fold 2 | fold 3 | fold 4 |
|---|---|---|---|---|---|
| Random Forest | 0.744 | 0.789 | 0.741 | 0.762 | 0.719 |
| LightGBM | 0.728 | 0.790 | 0.741 | 0.789 | 0.750 |
| XGBoost | 0.727 | 0.789 | 0.751 | 0.774 | 0.742 |

**Why LightGBM:**
- On held-out macro-F1, all three models are within 0.004 of each other (0.636 / 0.638 / 0.640).
  That is a tie at 0.01 resolution.
- On CV macro-F1, LightGBM (0.760) and XGBoost (0.757) also tie at 0.76, ahead of RF (0.75).
- LightGBM is simpler and faster than XGBoost: 1.2 s versus 3.9 s to fit, and its ONNX model is
  the production format chosen in TECH_STACK §5.2.
- **The honest reading is that the three models are statistically indistinguishable on the
  unseen coast.** XGBoost is nominally best on held-out accuracy and mangrove F1, and RF is
  the best calibrated (ECE 0.045 versus 0.08). The choice of algorithm matters far less here
  than the labels and features do.

### LightGBM, Gulf of Kutch: per class

| class | precision | recall | F1 | support |
|---|---:|---:|---:|---:|
| built_bare | 0.414 | 0.774 | 0.540 | 53 |
| cropland_aquaculture | 0.558 | 0.543 | 0.550 | 116 |
| **mangrove** | **0.982** | **0.911** | **0.945** | 123 |
| terrestrial_other | 0.583 | 0.619 | 0.600 | 97 |
| tidal_flat | 0.240 | 0.509 | 0.326 | 57 |
| water | 0.943 | 0.801 | 0.866 | 687 |

Binary mangrove-vs-rest F1 is 0.945.

### LightGBM, Gulf of Kutch: confusion matrix (rows = reference, columns = predicted)

| | built_bare | cropland_aq. | mangrove | terrestrial | tidal_flat | water |
|---|---:|---:|---:|---:|---:|---:|
| built_bare | 41 | 2 | 1 | 4 | 1 | 4 |
| cropland_aquaculture | 17 | 63 | 0 | 36 | 0 | 0 |
| mangrove | 0 | 8 | 112 | 2 | 0 | 1 |
| terrestrial_other | 7 | 29 | 0 | 60 | 1 | 0 |
| tidal_flat | 0 | 0 | 0 | 0 | 29 | 28 |
| water | 34 | 11 | 1 | 1 | 90 | 550 |

Pooled out-of-fold confusion over the CV folds (LightGBM, same row/column order):
`[[104,13,0,29,4,7],[24,1102,8,119,7,20],[9,41,4513,101,1,13],[55,279,63,2153,5,10],[8,7,2,3,108,97],[22,22,14,5,145,3868]]`.

### Feature importance (LightGBM gain, normalised)
The top features are ndwi_p10 (0.147), elevation (0.119), mvi (0.102), swir22 (0.082), swir16
(0.059), ndwi_p90 (0.053), ndvi_p10 (0.053) and ndre (0.038). The full list is in the metrics
JSON. The temporal NDWI low percentile (the wettest look) and elevation carry most of the
water / tidal-flat / land separation. MVI and SWIR carry the mangrove signal.

### ONNX export
`ml/artifacts/ecosystem_classifier_v1/model.onnx` (1.4 MB; input `input` float32 `[n, 30]`; outputs
`label` and `probabilities`). It was verified against native LightGBM on all 1,133 held-out
points:
- **100% identical predicted classes.**
- Max |probability difference| 0.014, from float32 tree thresholds versus float64.

The native model is also saved as `model.lgb.txt`, and the feature order, class order and fill
values are in `features.json`.

## Failure modes and limitations
1. **Water ↔ tidal flat on the unseen coast.** 90 of 687 Kutch water points are predicted as
   tidal flat, and 28 of 57 tidal flats as water. Tidal flats are also the weakest class in CV
   (F1 0.44). Kutch has very large intertidal mudflats. The tide at each acquisition is unknown,
   so the same pixel is "water" or "flat" depending on the tide (and on the label source's
   date). The F1 of this class is partly a property of the labels, not only of the model.
2. **Water → built/bare in Kutch.** 34 of 687 water points are predicted as built/bare. These are
   probably salt pans and evaporation ponds, which WorldCover labels as permanent water. They
   are spectrally bright and dry in the dry season.
3. **Cropland ↔ terrestrial vegetation.** This is the largest CV confusion (279 terrestrial →
   cropland, 119 cropland → terrestrial). Both classes are WorldCover-derived, with known
   class noise at 10 m, and aquaculture ponds are merged with cropland.
4. **Small classes.** built_bare (157 training points) and tidal_flat (225) have wide
   uncertainty, and fold-to-fold macro-F1 varies by 0.06.
5. **Label noise.** At least 59% of the labels (8,393 WorldCover 2021 negatives) are map-derived, plus 1996/2014
   coastTrain points re-checked only against GMW 2020. There is no manually QA'd gold set yet
   (planned: ~500 points, IMPLEMENTATION_PLAN §1b), so every score here is agreement with noisy
   maps, not ground truth.
6. **Temporal scope.** The model has only seen the dry season of 2020–21. Other years need
   re-validation before use, especially after the post-2022 BOA offset change, which
   `scale_and_mask` handles but which is untested for this model. The model also needs the same
   12-scene / 3-clear compositing.
7. **Extraction vs production.** The training features sample 20 m bands at the native pixel.
   Production `sentinel2.load` resamples them bilinearly to 10 m, a small expected difference.
   Sentinel-1 and slope were not used in v1.
8. **Calibration.** Probabilities are moderately calibrated (ECE 0.08). Recalibrate them, or
   take areas from a probability sample (Olofsson), rather than summing probabilities.

## Reproduce
```
uv sync --all-packages --group ml
uv run dvc repro features_extract    # ~3 h of requests; resumes from data/interim/features_parts
uv run dvc repro train_classifier    # ~19 min on 4 threads, peak RSS 0.93 GB
uv run mlflow ui --backend-store-uri sqlite:///mlruns/mlflow.db
```
