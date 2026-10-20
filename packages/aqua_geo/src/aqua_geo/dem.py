"""Copernicus DEM GLO-30 elevation on a Sentinel-2 pixel grid (classifier feature `elevation`).

Training (`ml/pipelines/features_extract.py`) sampled `cop-dem-glo-30` on Planetary Computer at
each label point (the native 30 m pixel containing it). Production reprojects the same collection
onto the project's 10 m UTM grid with **nearest-neighbour** resampling, so each 10 m pixel takes the
value of the DEM pixel containing its centre, which is the same rule.

Missing values: open sea has no DEM tile. Training filled the 8 such points with 0 m (sea level,
`features.json: fill_values`); here a pixel with no tile, or the band's nodata value, is NaN and the
classifier applies the same fill. A failed search or read is an error, never "missing".
"""

from collections.abc import Sequence
from typing import Any

import numpy as np
import pystac
import xarray as xr
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from aqua_geo.network import STAC_TIMEOUT_S, configure_odc_stac

COLLECTION = "cop-dem-glo-30"
ASSET = "data"
STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"


def search(geometry: BaseGeometry, *, stac_url: str = STAC_URL) -> list[pystac.Item]:
    """DEM tiles intersecting `geometry` (WGS84), unsigned. An empty list means open sea."""
    from pystac_client import Client

    client = Client.open(stac_url, timeout=STAC_TIMEOUT_S)
    found = client.search(collections=[COLLECTION], intersects=mapping(geometry), max_items=100)
    return sorted(found.items(), key=lambda item: item.id)


def sign(item: pystac.Item) -> pystac.Item:
    import planetary_computer

    signed: pystac.Item = planetary_computer.sign(item)
    return signed


def load_on_grid(
    items: Sequence[pystac.Item], like: xr.DataArray, epsg: int, *, chunk: int = 1024
) -> xr.DataArray:
    """Elevation (m, float32, NaN where missing) on the (y, x) grid of `like` (EPSG:`epsg`).

    `like` is any 2-D (or (time, y, x)) array on the target grid, e.g. the `clear` mask from
    `sentinel2.load`. Lazy (dask) when items are given; all-NaN when there is no tile."""
    import rioxarray  # noqa: F401  (registers the .rio accessor)

    grid = like.isel(time=0, drop=True) if "time" in like.dims else like
    if not items:
        return xr.full_like(grid, np.nan, dtype="float32").rename("elevation")

    import odc.geo.xr
    import odc.stac

    configure_odc_stac()
    geobox = grid.rio.write_crs(f"EPSG:{epsg}").odc.geobox
    ds = odc.stac.load(
        list(items),
        bands=[ASSET],
        geobox=geobox,
        resampling="nearest",
        chunks={"x": chunk, "y": chunk},
        fail_on_error=True,
    )
    raw = ds[ASSET]
    nodata: Any = raw.attrs.get("nodata")
    elevation = raw.astype("float32")
    if nodata is not None and np.isfinite(float(nodata)):
        elevation = elevation.where(raw != nodata)
    # Several tile dates collapse to one layer (tiles do not overlap; NaN-aware).
    if "time" in elevation.dims:
        elevation = elevation.max("time", skipna=True)
    out = elevation.assign_coords(y=grid.y.values, x=grid.x.values).astype("float32")
    out.attrs = {"units": "m", "source": COLLECTION}
    return out.drop_vars("spatial_ref", errors="ignore").rename("elevation")
