"""Per-scene zonal statistics for the project time series (nightly ingestion + backfill).

One Sentinel-2 scene clipped to a project polygon is reduced to a handful of numbers: the clear
(cloud-free, valid) fraction of the polygon and mean/P10/P50/P90 of each spectral index over the
clear pixels. These rows are cheap to store and are what loss alerts are computed from.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import xarray as xr

from aqua_geo.indices import add_indices

# Indices stored per scene (see aqua_geo.indices for the formulas).
TIMESERIES_INDICES: tuple[str, ...] = ("ndvi", "evi", "mvi", "ndwi", "mndwi", "ndmi", "cmri")

# MVI is unbounded where SWIR1 ≈ Green; clip extreme ratios so a few pixels cannot dominate.
_CLIP: dict[str, tuple[float, float]] = {"mvi": (-10.0, 10.0)}


@dataclass(frozen=True)
class SceneStats:
    polygon_pixels: int
    clear_pixels: int
    clear_fraction: float
    index_stats: dict[str, dict[str, Any]] = field(default_factory=dict)


def _summary(values: np.ndarray[Any, Any]) -> dict[str, Any]:
    valid = values[np.isfinite(values)]
    if valid.size == 0:
        return {"valid_pixels": 0}
    p10, p50, p90 = np.percentile(valid, [10, 50, 90])
    return {
        "valid_pixels": int(valid.size),
        "mean": round(float(valid.mean()), 4),
        "p10": round(float(p10), 4),
        "p50": round(float(p50), 4),
        "p90": round(float(p90), 4),
    }


def scene_statistics(
    scene: xr.Dataset,
    inside: xr.DataArray | np.ndarray[Any, Any],
    indices: Sequence[str] = TIMESERIES_INDICES,
) -> SceneStats:
    """Zonal statistics of ONE scene (dims (y, x), or (time=1, y, x)).

    `scene` holds masked reflectance (NaN where invalid) and a boolean `clear` mask, as returned
    by `aqua_geo.sentinel2.load`; `inside` is the polygon mask on the same grid
    (`aqua_geo.sentinel2.polygon_mask`). Index statistics use clear pixels inside the polygon.
    """
    if "time" in scene.dims:
        if scene.sizes["time"] != 1:
            raise ValueError("scene_statistics expects a single time step")
        scene = scene.isel(time=0)
    inside_np = np.asarray(inside, dtype=bool)
    clear_np = np.asarray(scene["clear"].values, dtype=bool) & inside_np
    polygon_pixels = int(inside_np.sum())
    clear_pixels = int(clear_np.sum())
    if polygon_pixels == 0:
        raise ValueError("the polygon covers no pixels of this scene")

    with_indices = add_indices(scene.drop_vars("clear"))
    stats: dict[str, dict[str, Any]] = {}
    for name in indices:
        values = np.asarray(with_indices[name].values, dtype="float64")
        values = np.where(clear_np, values, np.nan)
        if name in _CLIP:
            low, high = _CLIP[name]
            values = np.clip(values, low, high)
        stats[name] = _summary(values)
    return SceneStats(
        polygon_pixels=polygon_pixels,
        clear_pixels=clear_pixels,
        clear_fraction=round(clear_pixels / polygon_pixels, 4),
        index_stats=stats,
    )
