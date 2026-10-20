"""Sentinel-2 L2A access via STAC: search, load clipped to a polygon, harmonise, mask clouds.

Key correctness rules (each one was wrong or missing in the SIH prototype):
1. Read only pixels inside the project polygon (COG windowed reads), never whole 100 km tiles.
2. Convert DN to surface reflectance: (DN − offset) / 10000, where offset = 1000 for products with
   processing baseline ≥ 04.00 (from 25 Jan 2022) unless the provider already harmonised them
   (see `boa_offset` for the per-provider conventions, verified against live STAC items).
3. Mask clouds, cloud shadow, cirrus, snow, saturated and no-data pixels using the SCL layer.
4. Work in the local UTM zone at 10 m.
"""

import re
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

# Element 84 Earth Search v1, collection `sentinel-2-l2a` (public COGs on AWS, no signing). Asset
# keys are common band names (`red`, `nir`, `scl`, ...), so the canonical names map to themselves.
# Its DNs are harmonised per item: `earthsearch:boa_offset_applied: true` means Element 84 already
# removed the +1000 offset (checked 2026-10-20: the same 2024-01-14 T45QVC scene reads 1000 DN lower
# here than in `sentinel-2-c1-l2a`); items with baseline >= 04.00 and the flag false (e.g. Feb 2022)
# still carry it. Its `raster:bands` scale/offset (0.0001 / -0.1) is NOT reliable for flagged items
# and is never used.
EARTH_SEARCH = Provider(
    name="earth-search",
    stac_url="https://earth-search.aws.element84.com/v1",
    collection="sentinel-2-l2a",
    asset_names={name: name for name in BANDS} | {"scl": "scl"},
    already_harmonised=False,  # decided per item from earthsearch:boa_offset_applied
)

# Earth Search `sentinel-2-c1-l2a` (ESA Collection 1 reprocessing, baseline >= 05.00). Same asset
# keys; DNs are NOT harmonised (raster:bands offset -0.1 and the GeoTIFF offset tag agree), so the
# baseline rule applies. Coverage still has gaps (no Feb 2022 items over Bhitarkanika), hence it is
# not in the default fallback list.
EARTH_SEARCH_C1 = Provider(
    name="earth-search-c1",
    stac_url="https://earth-search.aws.element84.com/v1",
    collection="sentinel-2-c1-l2a",
    asset_names={name: name for name in BANDS} | {"scl": "scl"},
    already_harmonised=False,
)

PROVIDERS: dict[str, Provider] = {
    p.name: p for p in (PLANETARY_COMPUTER, EARTH_SEARCH, EARTH_SEARCH_C1)
}


def providers_from_names(names: Sequence[str]) -> list[Provider]:
    """Resolve an ordered list of provider names (settings `GEO_STAC_PROVIDERS`)."""
    unknown = [n for n in names if n not in PROVIDERS]
    if unknown:
        raise ValueError(f"unknown STAC provider(s) {unknown}; known: {sorted(PROVIDERS)}")
    if not names:
        raise ValueError("at least one STAC provider is required")
    return [PROVIDERS[n] for n in names]


_TILE = re.compile(r"_T?(\d{2}[C-X][A-Z]{2})_")


def scene_key(item_id: str, acquired_on: date) -> str:
    """Provider-independent identity of one Sentinel-2 acquisition: platform + MGRS tile + UTC
    date, parsed from the item id (Planetary Computer `S2A_MSIL2A_20240114T044139_R033_T45QVC_…`,
    Earth Search `S2A_45QVC_20240114_0_L2A` / `S2A_T45QVC_20240114T044648_L2A`). Item ids and
    `datetime` differ between providers (datatake start vs granule sensing time), so the item id
    alone cannot detect the same scene served by two catalogues. Ids without a tile (synthetic
    test scenes) are their own key."""
    tile = _TILE.search(item_id)
    if not item_id.startswith("S2") or tile is None:
        return item_id
    return f"{item_id[:3]}_{tile.group(1)}_{acquired_on:%Y%m%d}"


def utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180.0) // 6.0) + 1
    return (32600 if lat >= 0 else 32700) + zone


def boa_offset(item: pystac.Item, provider: Provider) -> float:
    """Offset to subtract from DNs for this item (0 if already harmonised).

    Planetary Computer: never harmonised, so 1000 for baseline >= 04.00. Earth Search
    `sentinel-2-l2a`: 0 when `earthsearch:boa_offset_applied` is true, otherwise the baseline rule.
    Earth Search `sentinel-2-c1-l2a`: the baseline rule (all Collection 1 items are >= 05.00)."""
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
