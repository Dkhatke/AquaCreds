"""Sentinel-1 RTC (radar backscatter) via Planetary Computer `sentinel-1-rtc`.

Radar sees through cloud, so it fills the June–September monsoon gap in the Sentinel-2 series
(RESEARCH_NOTES Q2). Data facts, checked against the collection and item metadata on 2026-10-20:
- Assets `vv` and `vh` (IW dual-pol, VV+VH over India): terrain-corrected gamma naught (γ⁰) in
  LINEAR power, float32 COGs at 10 m in the scene's UTM zone, nodata −32768.
- Processed by Catalyst from ESA GRD; licence CC BY 4.0 (attribution: Copernicus Sentinel data,
  processed by Catalyst, hosted by Microsoft Planetary Computer).
- The collection is flagged `msft:requires_account: true` ("A Planetary Computer account is required
  to retrieve SAS tokens to read the RTC data"). On 2026-10-20 an anonymous SAS token still allowed a
  ranged read, but that is not guaranteed: set `PC_SDK_SUBSCRIPTION_KEY` in production. A refused
  token surfaces as an HTTP 401/403 error, never as silent NaNs.

Processing per acquisition (one satellite pass = all slices of one absolute orbit):
1. Read VV and VH clipped to the project polygon, in local UTM at 10 m.
2. Invalid pixels (nodata, ≤ 0, NaN, outside the polygon) → NaN.
3. Speckle: single acquisitions have an equivalent number of looks of only ~4.4, so each band is
   median-filtered over a 3×3 window (NaN-aware, inside the polygon only) on LINEAR γ⁰ before
   any ratio, and the per-polygon P50 (itself a median over hundreds of pixels) is the headline
   value. Temporal medians across acquisitions belong to seasonal composites, not to this table.
4. Derived per pixel: VV/VH in dB (10·log10 γ⁰), VH/VV ratio (linear) and its dB difference, and the
   dual-pol Radar Vegetation Index RVI = 4·VH / (VV + VH) (linear γ⁰).
5. Per polygon: mean, P10, P50, P90 of each, plus the valid fraction.
"""

import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

import numpy as np
import pystac
import xarray as xr
from pystac_client import Client
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from aqua_geo.network import STAC_TIMEOUT_S, configure_odc_stac
from aqua_geo.sentinel2 import PLANETARY_COMPUTER, utm_epsg
from aqua_geo.timeseries import summarize

COLLECTION = "sentinel-1-rtc"
STAC_URL = PLANETARY_COMPUTER.stac_url
POLARIZATIONS: tuple[str, ...] = ("vv", "vh")
NODATA = -32768.0
SPECKLE_WINDOW = 3
SAR_METRICS: tuple[str, ...] = ("vv_db", "vh_db", "vh_vv_ratio", "vh_vv_db", "rvi")
SPECKLE_NOTE = (
    "3x3 NaN-aware median on linear gamma0 per acquisition, then per-polygon percentiles "
    "(P50 = per-polygon median); no temporal filtering"
)


def sign(item: pystac.Item) -> pystac.Item:
    """SAS-sign an RTC item (uses PC_SDK_SUBSCRIPTION_KEY when set)."""
    return PLANETARY_COMPUTER.sign(item)


@dataclass(frozen=True)
class Acquisition:
    """One Sentinel-1 pass over the polygon: every slice of one absolute orbit."""

    key: str  # e.g. "S1A_20240726_054932"
    acquired_at: datetime
    platform: str
    orbit_state: str | None
    relative_orbit: int | None
    absolute_orbit: int | None
    items: tuple[pystac.Item, ...] = field(compare=False, repr=False)

    @property
    def item_ids(self) -> list[str]:
        return [it.id for it in self.items]


def _when(item: pystac.Item) -> datetime:
    return item.datetime or datetime.fromisoformat(item.properties["datetime"])


def _dual_pol(item: pystac.Item) -> bool:
    pols = {str(p).upper() for p in item.properties.get("sar:polarizations", [])}
    return {"VV", "VH"} <= pols and all(p in item.assets for p in POLARIZATIONS)


def search(
    geometry: BaseGeometry,
    start: date,
    end: date,
    *,
    stac_url: str = STAC_URL,
    limit: int = 2000,
) -> list[pystac.Item]:
    """Unsigned VV+VH RTC items intersecting `geometry` (WGS84) in [start, end], by time.
    Sign with `sign()` right before reading (SAS tokens expire)."""
    client = Client.open(stac_url, timeout=STAC_TIMEOUT_S)
    found = client.search(
        collections=[COLLECTION],
        intersects=mapping(geometry),
        datetime=f"{start.isoformat()}/{end.isoformat()}",
        max_items=limit,
    )
    return sorted((it for it in found.items() if _dual_pol(it)), key=_when)


def group_acquisitions(items: Sequence[pystac.Item]) -> list[Acquisition]:
    """Group slices of the same pass (platform + absolute orbit); ascending and descending passes
    on one day stay separate (different geometry and time of day)."""
    groups: dict[tuple[str, Any, str], list[pystac.Item]] = {}
    for item in items:
        props = item.properties
        platform = str(props.get("platform", item.id[:3])).upper().replace("SENTINEL-", "S")
        orbit = props.get("sat:absolute_orbit")
        day = _when(item).date().isoformat() if orbit is None else ""
        groups.setdefault((platform, orbit, day), []).append(item)
    out = []
    for (platform, orbit, _day), members in groups.items():
        members.sort(key=_when)
        first = members[0].properties
        when = _when(members[0])
        rel = first.get("sat:relative_orbit")
        key = f"{platform}_{when:%Y%m%d}_{int(orbit):06d}" if orbit is not None else members[0].id
        out.append(
            Acquisition(
                key=key,
                acquired_at=when,
                platform=platform,
                orbit_state=first.get("sat:orbit_state"),
                relative_orbit=int(rel) if rel is not None else None,
                absolute_orbit=int(orbit) if orbit is not None else None,
                items=tuple(members),
            )
        )
    return sorted(out, key=lambda a: (a.acquired_at, a.key))


def mask_invalid(da: xr.DataArray) -> xr.DataArray:
    """Nodata (−32768), non-positive and non-finite γ⁰ → NaN (float32)."""
    values = da.astype("float32")
    ok = np.isfinite(values) & (values > 0) & (values != NODATA)
    return values.where(ok)


def load(
    items: Sequence[pystac.Item],
    geometry: BaseGeometry,
    *,
    resolution: float = 10.0,
    chunks: dict[str, int | Literal["auto"]] | None = None,
) -> xr.Dataset:
    """Load ONE acquisition (signed items of one pass) clipped to `geometry` in local UTM.

    Returns a Dataset with dims (time=1, y, x) holding linear γ⁰ `vv` and `vh` (NaN invalid) and
    attrs `epsg`. Slices of the same pass are fused into one plane."""
    import odc.stac
    from odc.geo.geom import Geometry

    if not items:
        raise ValueError("no items to load")
    configure_odc_stac()
    centroid = geometry.centroid
    epsg = utm_epsg(centroid.x, centroid.y)
    raw = odc.stac.load(
        items,
        bands=list(POLARIZATIONS),
        geopolygon=Geometry(mapping(geometry), crs="EPSG:4326"),
        crs=f"EPSG:{epsg}",
        resolution=resolution,
        groupby="solar_day",
        resampling="bilinear",
        chunks=chunks or {"x": 1024, "y": 1024},
        fail_on_error=True,
    )
    ds = xr.Dataset({p: mask_invalid(raw[p]) for p in POLARIZATIONS}, coords=raw.coords)
    ds.attrs["epsg"] = epsg
    return ds


def speckle_median(
    values: np.ndarray[Any, Any], size: int = SPECKLE_WINDOW
) -> np.ndarray[Any, Any]:
    """NaN-aware `size`×`size` median filter of a 2-D array. NaN pixels stay NaN (the filter never
    fills holes or grows the polygon); NaN neighbours are ignored."""
    if size < 1 or size % 2 == 0:
        raise ValueError("speckle window must be a positive odd number")
    arr = np.asarray(values, dtype="float64")
    if size == 1:
        return arr.copy()
    pad = size // 2
    padded = np.pad(arr, pad, mode="constant", constant_values=np.nan)
    windows = np.lib.stride_tricks.sliding_window_view(padded, (size, size))
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", "All-NaN slice", RuntimeWarning)
        filtered = np.nanmedian(windows, axis=(-2, -1))
    return np.where(np.isnan(arr), np.nan, filtered)


def to_db(linear: Any) -> Any:
    """10·log10(γ⁰); NaN for non-positive values."""
    arr = np.asarray(linear, dtype="float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(arr > 0, 10.0 * np.log10(arr), np.nan)


def rvi(vv: Any, vh: Any) -> Any:
    """Dual-pol Radar Vegetation Index 4·VH / (VV + VH) on linear γ⁰ (Trudel et al. 2012;
    Nasirzadehdizaji et al. 2019). Grows with volume scattering from canopy."""
    vv_a, vh_a = np.asarray(vv, dtype="float64"), np.asarray(vh, dtype="float64")
    den = vv_a + vh_a
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(den > 0, 4.0 * vh_a / den, np.nan)


def sar_metrics(vv: Any, vh: Any) -> dict[str, np.ndarray[Any, Any]]:
    """Per-pixel derived metrics from linear γ⁰ (see SAR_METRICS)."""
    vv_a, vh_a = np.asarray(vv, dtype="float64"), np.asarray(vh, dtype="float64")
    vv_db, vh_db = to_db(vv_a), to_db(vh_a)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(vv_a > 0, vh_a / vv_a, np.nan)
    return {
        "vv_db": vv_db,
        "vh_db": vh_db,
        "vh_vv_ratio": ratio,
        "vh_vv_db": vh_db - vv_db,
        "rvi": rvi(vv_a, vh_a),
    }


@dataclass(frozen=True)
class SarStats:
    polygon_pixels: int
    valid_pixels: int
    valid_fraction: float
    stats: dict[str, dict[str, Any]]


def acquisition_statistics(
    ds: xr.Dataset,
    inside: xr.DataArray | np.ndarray[Any, Any],
    *,
    speckle_window: int = SPECKLE_WINDOW,
) -> SarStats:
    """Per-polygon statistics of ONE acquisition (dims (y, x) or (time=1, y, x)) holding linear
    γ⁰ `vv` and `vh` (NaN invalid); `inside` is the polygon mask on the same grid."""
    if "time" in ds.dims:
        if ds.sizes["time"] != 1:
            raise ValueError("acquisition_statistics expects a single time step")
        ds = ds.isel(time=0)
    inside_np = np.asarray(inside, dtype=bool)
    polygon_pixels = int(inside_np.sum())
    if polygon_pixels == 0:
        raise ValueError("the polygon covers no pixels of this acquisition")
    vv = np.asarray(ds["vv"].values, dtype="float64")
    vh = np.asarray(ds["vh"].values, dtype="float64")
    valid = inside_np & np.isfinite(vv) & np.isfinite(vh) & (vv > 0) & (vh > 0)
    vv_f = speckle_median(np.where(valid, vv, np.nan), speckle_window)
    vh_f = speckle_median(np.where(valid, vh, np.nan), speckle_window)
    metrics = sar_metrics(vv_f, vh_f)
    valid_pixels = int(valid.sum())
    return SarStats(
        polygon_pixels=polygon_pixels,
        valid_pixels=valid_pixels,
        valid_fraction=round(valid_pixels / polygon_pixels, 4),
        stats={name: summarize(np.where(valid, metrics[name], np.nan)) for name in SAR_METRICS},
    )
