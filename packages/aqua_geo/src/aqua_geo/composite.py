"""Temporal compositing and zonal statistics."""

from collections.abc import Sequence
from typing import Any

import numpy as np
import xarray as xr

DRY_SEASON_MONTHS = (11, 12, 1, 2, 3)  # Nov–Mar: lowest cloud cover on most of India's coast


def season_filter(ds: xr.Dataset, months: Sequence[int] = DRY_SEASON_MONTHS) -> xr.Dataset:
    return ds.sel(time=ds.time.dt.month.isin(list(months)))


def median_composite(ds: xr.Dataset, *, min_clear: int = 3) -> xr.Dataset:
    """Per-pixel median of clear observations. Pixels with fewer than `min_clear` clear
    observations become NaN (an unreliable median is worse than none)."""
    clear_count = ds["clear"].sum("time")
    bands = [v for v in ds.data_vars if v != "clear"]
    med = ds[bands].median("time", skipna=True)
    enough = clear_count >= min_clear
    out = med.where(enough)
    out["clear_count"] = clear_count.astype("int16")
    return out


def percentile_composite(
    ds: xr.Dataset, variables: Sequence[str], quantiles: Sequence[float] = (0.1, 0.5, 0.9)
) -> xr.Dataset:
    """Per-pixel temporal percentiles, e.g. ndvi_p10 / ndvi_p50 / ndvi_p90 (captures seasonality
    and tidal variability, which single-date images miss)."""
    out: dict[str, xr.DataArray] = {}
    for var in variables:
        q = ds[var].quantile(list(quantiles), dim="time", skipna=True)
        for value in quantiles:
            out[f"{var}_p{round(value * 100):02d}"] = q.sel(quantile=value, drop=True).astype(
                "float32"
            )
    return xr.Dataset(out)


def zonal_summary(da: xr.DataArray) -> dict[str, Any]:
    """Statistics over all valid (non-NaN) pixels of a 2-D array."""
    values = np.asarray(da.values, dtype="float64").ravel()
    total = values.size
    valid = values[~np.isnan(values)]
    if valid.size == 0:
        return {"valid_pixels": 0, "total_pixels": int(total), "valid_fraction": 0.0}
    p10, p50, p90 = np.percentile(valid, [10, 50, 90])
    return {
        "valid_pixels": int(valid.size),
        "total_pixels": int(total),
        "valid_fraction": round(valid.size / total, 4),
        "mean": round(float(valid.mean()), 4),
        "std": round(float(valid.std()), 4),
        "p10": round(float(p10), 4),
        "p50": round(float(p50), 4),
        "p90": round(float(p90), 4),
    }
