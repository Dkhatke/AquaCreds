"""Spectral indices on surface reflectance (0–1). Inputs are numpy arrays or xarray objects.

Formulas are written out explicitly because several names are ambiguous in the literature
(notably "NDWI"). Every index divides via `_ratio`, which returns NaN instead of ±inf.
"""

from typing import Any

import numpy as np
import xarray as xr

EPS = 1e-6

# numpy arrays or xarray DataArrays; the functions are duck-typed over both, and numpy/xarray
# arithmetic is not precisely typed, so this is intentionally Any.
ArrayLike = Any

# Variable names used throughout the pipeline (Sentinel-2 band → name).
REQUIRED_FOR_ALL = ("blue", "green", "red", "rededge1", "nir", "nir08", "swir16")


def _ratio(num: ArrayLike, den: ArrayLike) -> ArrayLike:
    with np.errstate(divide="ignore", invalid="ignore"):
        out = num / den
    return (
        out.where(np.abs(den) > EPS)
        if isinstance(out, xr.DataArray)
        else np.where(np.abs(den) > EPS, out, np.nan)
    )


def ndvi(nir: ArrayLike, red: ArrayLike) -> ArrayLike:
    """Normalised Difference Vegetation Index (Rouse et al. 1974)."""
    return _ratio(nir - red, nir + red)


def evi(nir: ArrayLike, red: ArrayLike, blue: ArrayLike) -> ArrayLike:
    """Enhanced Vegetation Index (Huete et al. 2002). Requires reflectance in 0–1."""
    return 2.5 * _ratio(nir - red, nir + 6.0 * red - 7.5 * blue + 1.0)


def savi(nir: ArrayLike, red: ArrayLike, soil_l: float = 0.5) -> ArrayLike:
    """Soil-Adjusted Vegetation Index (Huete 1988)."""
    return (1.0 + soil_l) * _ratio(nir - red, nir + red + soil_l)


def ndwi(green: ArrayLike, nir: ArrayLike) -> ArrayLike:
    """McFeeters (1996) NDWI for open water: (Green − NIR) / (Green + NIR).
    NOT Gao's (1996) vegetation-water index — that one is `ndmi` here."""
    return _ratio(green - nir, green + nir)


def mndwi(green: ArrayLike, swir1: ArrayLike) -> ArrayLike:
    """Modified NDWI (Xu 2006): (Green − SWIR1) / (Green + SWIR1)."""
    return _ratio(green - swir1, green + swir1)


def ndmi(nir: ArrayLike, swir1: ArrayLike) -> ArrayLike:
    """Normalised Difference Moisture Index (Gao 1996 NDWI): (NIR − SWIR1) / (NIR + SWIR1)."""
    return _ratio(nir - swir1, nir + swir1)


def mvi(green: ArrayLike, nir: ArrayLike, swir1: ArrayLike) -> ArrayLike:
    """Mangrove Vegetation Index (Baloloy et al. 2020): (NIR − Green) / (SWIR1 − Green)."""
    return _ratio(nir - green, swir1 - green)


def cmri(nir: ArrayLike, red: ArrayLike, green: ArrayLike) -> ArrayLike:
    """Combined Mangrove Recognition Index (Gupta et al. 2018): NDVI − NDWI."""
    return ndvi(nir, red) - ndwi(green, nir)


def ndre(nir_narrow: ArrayLike, rededge1: ArrayLike) -> ArrayLike:
    """Normalised Difference Red-Edge, Sentinel-2 B8A and B5."""
    return _ratio(nir_narrow - rededge1, nir_narrow + rededge1)


def add_indices(ds: xr.Dataset) -> xr.Dataset:
    """Return `ds` with all indices added as float32 variables."""
    missing = [b for b in REQUIRED_FOR_ALL if b not in ds]
    if missing:
        raise KeyError(f"Dataset is missing bands: {missing}")
    indices: dict[str, Any] = {
        "ndvi": ndvi(ds.nir, ds.red),
        "evi": evi(ds.nir, ds.red, ds.blue),
        "savi": savi(ds.nir, ds.red),
        "ndwi": ndwi(ds.green, ds.nir),
        "mndwi": mndwi(ds.green, ds.swir16),
        "ndmi": ndmi(ds.nir, ds.swir16),
        "mvi": mvi(ds.green, ds.nir, ds.swir16),
        "cmri": cmri(ds.nir, ds.red, ds.green),
        "ndre": ndre(ds.nir08, ds.rededge1),
    }
    return ds.assign({name: value.astype("float32") for name, value in indices.items()})
