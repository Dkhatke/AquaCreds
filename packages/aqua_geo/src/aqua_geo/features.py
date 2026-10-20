"""Per-pixel classifier features from a masked reflectance time series.

This is the single definition of the ecosystem-classifier feature vector. Training
(`ml/pipelines/features_extract.py`, point time series) and production (worker, pixel stacks
loaded with `sentinel2.load`) both call `pixel_features`, so the model always sees features
computed the same way.

Input: an xarray Dataset with a `time` dimension and any other dims (e.g. `y, x` or `point`),
holding reflectance variables named as in `sentinel2.BANDS` (NaN where invalid) and a boolean
`clear` mask, i.e. the output of `sentinel2.scale_and_mask` / `sentinel2.load`.
"""

import warnings
from collections.abc import Sequence

import numpy as np
import xarray as xr

from aqua_geo.composite import median_composite, percentile_composite
from aqua_geo.indices import add_indices
from aqua_geo.sentinel2 import BANDS

INDEX_VARS: tuple[str, ...] = (
    "ndvi",
    "evi",
    "savi",
    "ndwi",
    "mndwi",
    "ndmi",
    "mvi",
    "cmri",
    "ndre",
)
PERCENTILE_VARS: tuple[str, ...] = ("ndvi", "mvi", "ndwi")
QUANTILES: tuple[float, ...] = (0.1, 0.5, 0.9)
MIN_CLEAR = 3

# Deterministic feature order: band medians, index medians, temporal percentiles, clear count.
FEATURE_VARS: tuple[str, ...] = (
    *BANDS,
    *INDEX_VARS,
    *(f"{v}_p{round(q * 100):02d}" for v in PERCENTILE_VARS for q in QUANTILES),
    "clear_count",
)


def pixel_features(refl: xr.Dataset, *, min_clear: int = MIN_CLEAR) -> xr.Dataset:
    """Indices per scene, then temporal aggregation: median of every band and index (NaN where
    fewer than `min_clear` clear observations), p10/p50/p90 of NDVI/MVI/NDWI, clear count.

    Indices are computed per scene *before* aggregation (the median of a ratio is not the ratio
    of medians). Returns a Dataset with the variables in `FEATURE_VARS` and the non-time dims.
    """
    if "time" not in refl.dims:
        raise ValueError("refl must have a time dimension")
    missing = [b for b in BANDS if b not in refl]
    if missing:
        raise KeyError(f"Dataset is missing bands: {missing}")
    ds = add_indices(refl[[*BANDS, "clear"]])
    with warnings.catch_warnings():
        # Pixels that are never clear are all-NaN along time; NaN is the intended result.
        warnings.filterwarnings("ignore", "All-NaN slice", RuntimeWarning)
        med = median_composite(ds, min_clear=min_clear)
        pct = percentile_composite(ds, PERCENTILE_VARS, QUANTILES)
    # Percentiles from fewer than `min_clear` looks are as unreliable as the median.
    pct = pct.where(med["clear_count"] >= min_clear)
    out = xr.merge([med, pct], compat="override")
    return out[list(FEATURE_VARS)]


def sample_points(
    ds: xr.Dataset,
    x: Sequence[float] | np.ndarray,
    y: Sequence[float] | np.ndarray,
    *,
    dim: str = "point",
) -> xr.Dataset:
    """Nearest-pixel values of a gridded (…, y, x) Dataset at map coordinates `x`, `y` (same CRS
    as the dataset). Points farther than one pixel outside the grid raise ValueError rather than
    silently snapping to the edge. Returns the Dataset with (y, x) replaced by `dim`."""
    xs = np.asarray(x, dtype="float64")
    ys = np.asarray(y, dtype="float64")
    if xs.shape != ys.shape or xs.ndim != 1:
        raise ValueError("x and y must be 1-D and of equal length")
    if xs.size == 0:
        raise ValueError("no points to sample")
    res_x = abs(float(ds.x[1] - ds.x[0])) if ds.sizes["x"] > 1 else 0.0
    res_y = abs(float(ds.y[1] - ds.y[0])) if ds.sizes["y"] > 1 else 0.0
    x0, x1 = float(ds.x.min()) - res_x, float(ds.x.max()) + res_x
    y0, y1 = float(ds.y.min()) - res_y, float(ds.y.max()) + res_y
    outside = (xs < x0) | (xs > x1) | (ys < y0) | (ys > y1)
    if outside.any():
        raise ValueError(f"{int(outside.sum())} point(s) fall outside the dataset grid")
    return ds.sel(
        x=xr.DataArray(xs, dims=dim), y=xr.DataArray(ys, dims=dim), method="nearest"
    ).drop_vars(["x", "y"])
