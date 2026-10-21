"""Ecosystem-classifier inference on a project's pixel stack, and the area maths.

Pure functions: no network, no object storage, no ONNX runtime (the model is passed in as a
`Predictor`, so this package stays free of inference dependencies and tests can use any model).

Feature vector (must match training exactly, see `ml/model_cards/ecosystem_classifier_v1.md`):
1. `aqua_geo.features.pixel_features` on the masked reflectance stack of the dry season, the same
   function the training extraction called: band medians, per-scene indices then their medians,
   temporal p10/p50/p90 of NDVI/MVI/NDWI, all NaN where a pixel has fewer than `min_clear` clear
   looks.
2. `elevation` from Copernicus DEM GLO-30 (`aqua_geo.dem`), with the model's fill values
   (`features.json: fill_values`, 0 m where the DEM has no tile).
3. Columns in `features.json: feature_columns` order, cast to float32 (the ONNX input type).

A pixel gets a prediction only if its centre is inside the project polygon, it has at least
`min_clear` clear looks (training dropped points with fewer), and every feature is finite after
the fill (training had no other missing values, so the model never saw NaN inputs). Every other
in-polygon pixel is *unclassified*: counted and reported, never assigned a class.

Memory: the stack is processed in square tiles (`tile` pixels): only one tile of every scene is in
memory at a time, which bounds peak memory independently of the project size.

Areas: count of pixels of a class x pixel area of the 10 m UTM grid / UTM areal scale factor at the
polygon centroid. UTM is conformal, not equal-area; its areal scale factor k² is 0.9992 on the
central meridian and up to 1.0019 at a zone edge on India's coast (pyproj, 3° off the meridian at
8° N), so the correction changes areas by at most ~0.2 %; within one project it varies far less.
Pixels count when their centre lies inside the polygon (`sentinel2.polygon_mask`).
"""

import json
import math
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import xarray as xr

from aqua_geo.features import FEATURE_VARS, pixel_features

# The feature vector production can build: pixel_features (minus the clear count, which training
# excluded because it describes cloudiness) + DEM elevation.
PRODUCTION_FEATURES: tuple[str, ...] = (
    *(v for v in FEATURE_VARS if v != "clear_count"),
    "elevation",
)
NODATA_CLASS = 255
SQM_PER_HA = 10_000.0

# (n, n_features) float32 → (class index per row, probability per row and class)
Predictor = Callable[[np.ndarray], tuple[np.ndarray, np.ndarray]]


@dataclass(frozen=True)
class ModelSpec:
    """What the model expects, from its `features.json`."""

    version: str
    feature_columns: tuple[str, ...]
    classes: tuple[str, ...]
    fill_values: Mapping[str, float]
    min_clear: int
    positive_class: str

    @classmethod
    def from_features_json(cls, raw: bytes | str | Mapping[str, Any]) -> "ModelSpec":
        doc: Mapping[str, Any] = raw if isinstance(raw, Mapping) else json.loads(raw)
        spec = cls(
            version=str(doc["model_version"]),
            feature_columns=tuple(str(c) for c in doc["feature_columns"]),
            classes=tuple(str(c) for c in doc["classes"]),
            fill_values={str(k): float(v) for k, v in (doc.get("fill_values") or {}).items()},
            min_clear=int(doc["min_clear"]),
            positive_class=str(doc.get("positive_class", "mangrove")),
        )
        spec.validate()
        return spec

    def validate(self) -> None:
        if self.feature_columns != PRODUCTION_FEATURES:
            raise ValueError(
                "model feature columns differ from the vector production builds: "
                f"model {list(self.feature_columns)} vs production {list(PRODUCTION_FEATURES)}"
            )
        if not 1 < len(self.classes) < NODATA_CLASS:
            raise ValueError(f"unsupported number of classes: {len(self.classes)}")
        if self.positive_class not in self.classes:
            raise ValueError(f"positive class {self.positive_class!r} is not a model class")
        unknown = set(self.fill_values) - set(self.feature_columns)
        if unknown:
            raise ValueError(f"fill values for unknown features: {sorted(unknown)}")
        if self.min_clear < 1:
            raise ValueError("min_clear must be >= 1")

    @property
    def positive_index(self) -> int:
        return self.classes.index(self.positive_class)


@dataclass(frozen=True)
class TileFeatures:
    matrix: np.ndarray  # (n_valid, n_features) float32, spec.feature_columns order
    index: np.ndarray  # flat (row-major) indices of those pixels within the tile
    too_few_clear: int  # in-polygon pixels with < min_clear clear looks
    nonfinite: int  # in-polygon pixels with enough looks but a non-finite feature


def tile_features(
    refl: xr.Dataset,
    elevation: np.ndarray,
    inside: np.ndarray,
    spec: ModelSpec,
) -> TileFeatures:
    """Feature matrix of the in-polygon pixels of one (time, y, x) tile.

    `refl`: masked reflectance + `clear` (the output of `sentinel2.scale_and_mask` / `load`),
    numpy-backed or lazy; `elevation`: (y, x) metres with NaN where missing; `inside`: (y, x)."""
    if refl["clear"].dims != ("time", "y", "x"):
        raise ValueError(f"expected (time, y, x), got {refl['clear'].dims}")
    shape = (refl.sizes["y"], refl.sizes["x"])
    elev = np.asarray(elevation, dtype="float32")
    ins = np.asarray(inside, dtype=bool)
    if elev.shape != shape or ins.shape != shape:
        raise ValueError(f"elevation {elev.shape} / inside {ins.shape} do not match grid {shape}")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN pixels → NaN, handled below
        feats = pixel_features(refl, min_clear=spec.min_clear).load()
    columns = [
        elev if name == "elevation" else np.asarray(feats[name].values, dtype="float32")
        for name in spec.feature_columns
    ]
    matrix = np.stack([c.reshape(-1) for c in columns], axis=1).astype("float32", copy=False)
    for name, value in spec.fill_values.items():
        col = spec.feature_columns.index(name)
        missing = np.isnan(matrix[:, col])
        matrix[missing, col] = value
    candidate = ins.reshape(-1)
    clear_ok = np.asarray(feats["clear_count"].values).reshape(-1) >= spec.min_clear
    finite = np.isfinite(matrix).all(axis=1)
    valid = candidate & clear_ok & finite
    return TileFeatures(
        matrix=matrix[valid],
        index=np.flatnonzero(valid),
        too_few_clear=int((candidate & ~clear_ok).sum()),
        nonfinite=int((candidate & clear_ok & ~finite).sum()),
    )


@dataclass
class ClassifiedGrid:
    labels: np.ndarray  # (y, x) uint8 class index, NODATA_CLASS where no prediction
    max_probability: np.ndarray  # (y, x) float32 highest class probability, NaN where none
    polygon_pixels: int = 0
    classified_pixels: int = 0
    too_few_clear: int = 0
    nonfinite: int = 0
    tiles: int = 0


def _predict_checked(
    predict: Predictor, matrix: np.ndarray, n_classes: int
) -> tuple[np.ndarray, np.ndarray]:
    labels, probs = predict(matrix)
    labels = np.asarray(labels).reshape(-1)
    probs = np.asarray(probs, dtype="float32")
    if labels.shape[0] != matrix.shape[0] or probs.shape != (matrix.shape[0], n_classes):
        raise ValueError(
            f"model returned labels {labels.shape} / probabilities {probs.shape} for "
            f"{matrix.shape[0]} rows and {n_classes} classes"
        )
    if labels.size and (labels.min() < 0 or labels.max() >= n_classes):
        raise ValueError("model returned a class index outside the class list")
    return labels.astype("uint8"), probs


def classify_stack(
    refl: xr.Dataset,
    elevation: xr.DataArray | np.ndarray,
    inside: xr.DataArray | np.ndarray,
    predict: Predictor,
    spec: ModelSpec,
    *,
    tile: int = 256,
) -> ClassifiedGrid:
    """Predict every in-polygon pixel of a (time, y, x) stack, `tile` x `tile` pixels at a time.

    `refl` and `elevation` may be lazy (dask): each tile is loaded, featurised and predicted,
    then released, so memory is bounded by one tile of every scene."""
    if tile < 1:
        raise ValueError("tile must be >= 1")
    ny, nx = refl.sizes["y"], refl.sizes["x"]
    ins_all = np.asarray(inside, dtype=bool)
    if ins_all.shape != (ny, nx):
        raise ValueError(f"inside {ins_all.shape} does not match grid {(ny, nx)}")
    grid = ClassifiedGrid(
        labels=np.full((ny, nx), NODATA_CLASS, dtype="uint8"),
        max_probability=np.full((ny, nx), np.nan, dtype="float32"),
        polygon_pixels=int(ins_all.sum()),
    )
    n_classes = len(spec.classes)
    for y0 in range(0, ny, tile):
        for x0 in range(0, nx, tile):
            sy, sx = slice(y0, min(y0 + tile, ny)), slice(x0, min(x0 + tile, nx))
            ins = ins_all[sy, sx]
            if not ins.any():
                continue  # no pixel of this tile is in the polygon: nothing is read
            sub = refl.isel(y=sy, x=sx).load()
            if isinstance(elevation, xr.DataArray):
                elev = np.asarray(elevation.isel(y=sy, x=sx).values)
            else:
                elev = np.asarray(elevation)[sy, sx]
            feats = tile_features(sub, elev, ins, spec)
            grid.tiles += 1
            grid.too_few_clear += feats.too_few_clear
            grid.nonfinite += feats.nonfinite
            if not feats.index.size:
                continue
            labels, probs = _predict_checked(predict, feats.matrix, n_classes)
            tile_labels = grid.labels[sy, sx]  # views into the output grid
            tile_prob = grid.max_probability[sy, sx]
            rows, cols = np.unravel_index(feats.index, ins.shape)
            tile_labels[rows, cols] = labels
            tile_prob[rows, cols] = probs.max(axis=1)
            grid.classified_pixels += int(feats.index.size)
    return grid


# ---------------------------------------------------------------- areas


def grid_pixel_area_m2(x: Sequence[float] | np.ndarray, y: Sequence[float] | np.ndarray) -> float:
    """Pixel area of a regular grid from its centre coordinates (map units², metres for UTM)."""
    xs, ys = np.asarray(x, dtype="float64"), np.asarray(y, dtype="float64")
    if xs.size < 2 or ys.size < 2:
        raise ValueError("need at least 2 pixels along each axis to derive the pixel size")
    dx, dy = np.diff(xs), np.diff(ys)
    if not (np.allclose(dx, dx[0]) and np.allclose(dy, dy[0])):
        raise ValueError("grid is not regular")
    return float(abs(dx[0]) * abs(dy[0]))


def utm_areal_scale(epsg: int, lon: float, lat: float) -> float:
    """Areal scale factor (map area / true area) of the projected CRS at (lon, lat)."""
    from pyproj import CRS, Proj

    crs = CRS.from_epsg(epsg)
    if not crs.is_projected:
        raise ValueError(f"EPSG:{epsg} is not a projected CRS")
    scale = float(Proj(crs).get_factors(lon, lat).areal_scale)
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError(f"invalid areal scale {scale} at ({lon}, {lat})")
    return scale


@dataclass(frozen=True)
class AreaSummary:
    class_areas_ha: dict[str, float]
    mangrove_ha: float
    mangrove_pixels: int
    mangrove_mean_confidence: float | None
    low_confidence_threshold: float
    low_confidence_share: float
    low_confidence_pixels: int
    polygon_pixels: int
    classified_pixels: int
    unclassified_pixels: int
    unclassified_ha: float
    pixel_area_m2: float
    areal_scale: float
    class_pixels: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return asdict(self)


def area_summary(
    grid: ClassifiedGrid,
    inside: xr.DataArray | np.ndarray,
    spec: ModelSpec,
    *,
    pixel_area_m2: float,
    areal_scale: float,
    low_confidence_threshold: float,
) -> AreaSummary:
    """Per-class areas (ha) inside the polygon, the positive-class (mangrove) area and confidence.

    - area(class) = pixels of the class x pixel_area_m2 / areal_scale / 10 000 (rounded to 1 m²);
    - mangrove mean confidence = mean highest-class probability over mangrove pixels;
    - low-confidence share = classified pixels with highest probability < threshold / classified.
    """
    if pixel_area_m2 <= 0 or areal_scale <= 0:
        raise ValueError("pixel area and areal scale must be positive")
    ins = np.asarray(inside, dtype=bool)
    labels = grid.labels[ins]
    probs = grid.max_probability[ins]
    true_m2 = pixel_area_m2 / areal_scale

    def ha(pixels: int) -> float:
        return round(pixels * true_m2 / SQM_PER_HA, 4)

    counts = {name: int((labels == i).sum()) for i, name in enumerate(spec.classes)}
    classified = int(sum(counts.values()))
    unclassified = int(ins.sum()) - classified
    pos = labels == spec.positive_index
    pos_probs = probs[pos]
    classified_probs = probs[labels != NODATA_CLASS]
    low = int((classified_probs < low_confidence_threshold).sum())
    return AreaSummary(
        class_areas_ha={name: ha(n) for name, n in counts.items()},
        mangrove_ha=ha(counts[spec.positive_class]),
        mangrove_pixels=counts[spec.positive_class],
        mangrove_mean_confidence=(round(float(pos_probs.mean()), 6) if pos_probs.size else None),
        low_confidence_threshold=low_confidence_threshold,
        low_confidence_share=round(low / classified, 6) if classified else 0.0,
        low_confidence_pixels=low,
        polygon_pixels=int(ins.sum()),
        classified_pixels=classified,
        unclassified_pixels=unclassified,
        unclassified_ha=ha(unclassified),
        pixel_area_m2=pixel_area_m2,
        areal_scale=round(areal_scale, 8),
        class_pixels=counts,
    )
