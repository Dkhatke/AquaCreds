"""Sentinel-2 L2A access via STAC: search, load clipped to a polygon, harmonise, mask clouds.

Key correctness rules (each one was wrong or missing in the SIH prototype):
1. Read only pixels inside the project polygon (COG windowed reads), never whole 100 km tiles.
2. Convert DN to surface reflectance: (DN − offset) / 10000, where offset = 1000 for products with
   processing baseline ≥ 04.00 (from 25 Jan 2022) unless the provider already harmonised them.
3. Mask clouds, cloud shadow, cirrus, snow, saturated and no-data pixels using the SCL layer.
4. Work in the local UTM zone at 10 m.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

import numpy as np
import pystac
import xarray as xr
from pystac_client import Client
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from aqua_geo.network import STAC_TIMEOUT_S, configure_odc_stac

# pipeline name → Sentinel-2 band id
BANDS: dict[str, str] = {
    "blue": "B02",
    "green": "B03",
    "red": "B04",
    "rededge1": "B05",
    "rededge2": "B06",
    "rededge3": "B07",
    "nir": "B08",
    "nir08": "B8A",
    "swir16": "B11",
    "swir22": "B12",
}

# Scene Classification Layer values treated as invalid:
# 0 no data, 1 saturated/defective, 3 cloud shadow, 8 cloud medium prob., 9 cloud high prob.,
# 10 thin cirrus, 11 snow/ice.  (2 dark area, 4 vegetation, 5 bare, 6 water, 7 unclassified kept)
SCL_INVALID: frozenset[int] = frozenset({0, 1, 3, 8, 9, 10, 11})

QUANTIFICATION = 10_000.0
BOA_OFFSET = 1_000.0
OFFSET_BASELINE = "04.00"


@dataclass(frozen=True)
class Provider:
    name: str
    stac_url: str
    collection: str
    asset_names: dict[str, str]  # pipeline name (+ "scl") → STAC asset key
    sign: Callable[[pystac.Item], pystac.Item] = field(default=lambda item: item)
    already_harmonised: bool = False


def _pc_sign(item: pystac.Item) -> pystac.Item:
    import planetary_computer

    signed: pystac.Item = planetary_computer.sign(item)
    return signed


PLANETARY_COMPUTER = Provider(
    name="planetary-computer",
    stac_url="https://planetarycomputer.microsoft.com/api/stac/v1",
    collection="sentinel-2-l2a",
    asset_names={**BANDS, "scl": "SCL"},
    sign=_pc_sign,
    already_harmonised=False,
)

EARTH_SEARCH = Provider(
    name="earth-search",
    stac_url="https://earth-search.aws.element84.com/v1",
    collection="sentinel-2-c1-l2a",  # Collection 1: offsets already removed
    asset_names={name: name for name in BANDS} | {"scl": "scl"},
    already_harmonised=True,
)


def utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180.0) // 6.0) + 1
    return (32600 if lat >= 0 else 32700) + zone


def boa_offset(item: pystac.Item, provider: Provider) -> float:
    """Offset to subtract from DNs for this item (0 if already harmonised)."""
    props = item.properties
    if provider.already_harmonised or props.get("earthsearch:boa_offset_applied") is True:
        return 0.0
    baseline = str(props.get("s2:processing_baseline", "00.00"))
    return BOA_OFFSET if baseline >= OFFSET_BASELINE else 0.0


def search(
    geometry: BaseGeometry,
    start: date,
    end: date,
    *,
    provider: Provider = PLANETARY_COMPUTER,
    max_cloud: float = 70.0,
    limit: int = 500,
    sign: bool = True,
) -> list[pystac.Item]:
    """Scenes intersecting `geometry` (WGS84) in [start, end], sorted by acquisition time.

    Pass `sign=False` for long jobs and call `provider.sign(item)` right before each read:
    Planetary Computer SAS tokens expire after ~45 minutes."""
    client = Client.open(provider.stac_url, timeout=STAC_TIMEOUT_S)
    found = client.search(
        collections=[provider.collection],
        intersects=mapping(geometry),
        datetime=f"{start.isoformat()}/{end.isoformat()}",
        query={"eo:cloud_cover": {"lt": max_cloud}},
        max_items=limit,
    )
    items = sorted(found.items(), key=lambda it: it.datetime or it.properties["datetime"])
    return [provider.sign(item) for item in items] if sign else items


def scale_and_mask(
    raw: xr.Dataset, offsets: Sequence[float], band_names: Sequence[str]
) -> xr.Dataset:
    """DN → reflectance with per-time offsets; invalid SCL / DN==0 pixels → NaN.

    `raw` must have dims (time, y, x), integer band variables and an `scl` variable.
    """
    if raw.sizes["time"] != len(offsets):
        raise ValueError("one offset per time step is required")
    offset = xr.DataArray(np.asarray(offsets, dtype="float32"), dims="time")
    valid = ~raw.scl.isin(sorted(SCL_INVALID))
    out: dict[str, xr.DataArray] = {}
    for name in band_names:
        dn = raw[name].astype("float32")
        refl = (dn - offset) / QUANTIFICATION
        out[name] = refl.where(valid & (raw[name] > 0)).clip(min=0.0).astype("float32")
    result = xr.Dataset(out, coords=raw.coords, attrs=raw.attrs)
    result["clear"] = valid & (raw[band_names[0]] > 0)
    return result


def load(
    items: Sequence[pystac.Item],
    geometry: BaseGeometry,
    *,
    provider: Provider = PLANETARY_COMPUTER,
    bands: Sequence[str] = tuple(BANDS),
    resolution: float = 10.0,
    chunks: dict[str, int | Literal["auto"]] | None = None,
) -> xr.Dataset:
    """Load masked surface reflectance clipped to `geometry` (WGS84) in local UTM at `resolution`.

    Returns a lazy (dask) Dataset with dims (time, y, x): one variable per band (reflectance
    0–1, NaN where invalid) plus a boolean `clear` mask. Pixels outside the polygon are NaN.
    """
    import odc.stac
    from odc.geo.geom import Geometry

    if not items:
        raise ValueError("no items to load")
    configure_odc_stac()
    centroid = geometry.centroid
    epsg = utm_epsg(centroid.x, centroid.y)
    asset_keys = [provider.asset_names[b] for b in (*bands, "scl")]
    raw = odc.stac.load(
        items,
        bands=asset_keys,
        geopolygon=Geometry(mapping(geometry), crs="EPSG:4326"),
        crs=f"EPSG:{epsg}",
        resolution=resolution,
        groupby="solar_day",
        resampling={"*": "bilinear", provider.asset_names["scl"]: "nearest"},
        chunks=chunks or {"x": 1024, "y": 1024},
        fail_on_error=True,  # never turn failed reads into silent NaNs
    )
    raw = raw.rename({provider.asset_names[b]: b for b in (*bands, "scl")})

    # One offset per (solar-day) time step; items from the same day share a processing baseline.
    by_day: dict[Any, float] = {}
    for item in items:
        day = np.datetime64(
            item.datetime.date() if item.datetime else item.properties["datetime"][:10]
        )
        by_day.setdefault(day, boa_offset(item, provider))
    offsets = [by_day.get(np.datetime64(str(t)[:10]), 0.0) for t in raw.time.values]

    ds = scale_and_mask(raw, offsets, bands)
    return clip_to_geometry(ds, geometry, epsg)


def polygon_mask(template: xr.DataArray, geometry: BaseGeometry, epsg: int) -> xr.DataArray:
    """Boolean (y, x) mask of the pixels of `template`'s grid whose centres fall inside
    `geometry` (WGS84). `template` must have y/x coordinates in EPSG:`epsg`."""
    import rioxarray  # noqa: F401  (registers the .rio accessor)
    import shapely
    from pyproj import Transformer

    to_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    local = shapely.transform(
        geometry, lambda xy: np.column_stack(to_utm.transform(xy[:, 0], xy[:, 1]))
    )
    grid = template.isel(time=0) if "time" in template.dims else template
    # float32 template: rioxarray fills outside pixels with NaN (an integer template would cast
    # NaN to an undefined integer value).
    mask: xr.DataArray = (
        xr.ones_like(grid, dtype="float32")
        .rio.write_crs(f"EPSG:{epsg}")
        .rio.clip([mapping(local)], drop=False, all_touched=False)
        .fillna(0)
        > 0
    )
    return mask


def clip_to_geometry(ds: xr.Dataset, geometry: BaseGeometry, epsg: int) -> xr.Dataset:
    """Set pixels outside the polygon to NaN / False (keeps the rectangular grid)."""
    import rioxarray  # noqa: F401  (registers the .rio accessor)

    ds = ds.rio.write_crs(f"EPSG:{epsg}")
    inside = polygon_mask(ds["clear"], geometry, epsg)
    out = ds.where(inside)
    out["clear"] = ds["clear"] & inside
    out.attrs["epsg"] = epsg
    return out
