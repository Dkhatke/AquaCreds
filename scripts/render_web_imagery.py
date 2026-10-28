"""Render the web app's hero / map imagery from real Sentinel-2 L2A data (Planetary Computer).

Usage:  uv run python scripts/render_web_imagery.py [--out apps/web/public/imagery]

Small reads on purpose: one dry-season window, the two least-cloudy acquisition days (all tiles of each pass), four bands + SCL,
read at 20 m (COG overviews) over a ~36 x 33 km box around Bhitarkanika (Odisha). Clouds are
masked with SCL (aqua_geo.sentinel2) and the first clear look wins (least cloudy scene first).

Writes, per product (true colour B04/B03/B02, false colour B08/B04/B03):
    <name>.webp           UTM render for the landing page (<= 1.5 MB)
    <name>-wgs84.webp     the same composite resampled to EPSG:4326 for a MapLibre image source
    provenance.json       scene ids, dates, bbox, processing, attribution
"""

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import xarray as xr
from PIL import Image
from shapely.geometry import box, shape
from shapely.ops import unary_union

from aqua_geo import __version__ as geo_version
from aqua_geo import sentinel2 as s2

ROOT = Path(__file__).resolve().parents[1]
BBOX = (86.70, 20.52, 87.05, 20.82)  # lon_min, lat_min, lon_max, lat_max: Bhitarkanika
WINDOW = (date(2024, 11, 1), date(2025, 3, 31))  # dry season: fewest clouds over Odisha
RESOLUTION_M = 20.0
MAX_DAYS = 2  # a second day only fills pixels that are cloudy on the first
MAX_BYTES = 1_500_000

PRODUCTS = {
    # name: (bands as R, G, B), percentile stretch, gamma, one stretch for all bands
    "bhitarkanika-truecolour": (("red", "green", "blue"), (1.0, 99.0), 0.8, True),
    "bhitarkanika-falsecolour": (("nir", "red", "green"), (2.0, 99.0), 1.0, False),
}


def first_clear(ds: xr.Dataset, order: list[int]) -> xr.Dataset:
    """Per pixel, the value from the first time step (in `order`) that is clear."""
    out = ds.isel(time=order[0])
    for t in order[1:]:
        out = out.fillna(ds.isel(time=t))
    return out


def stretch(rgb: np.ndarray, pct: tuple[float, float], gamma: float, joint: bool) -> np.ndarray:
    """Percentile stretch to 0-255 (one range for all bands when `joint`, keeping natural colour
    balance); NaN (no clear look) becomes transparent later."""
    out = np.zeros(rgb.shape, dtype="float32")
    joint_range = np.nanpercentile(rgb, pct) if joint else None
    for i in range(3):
        band = rgb[..., i]
        lo, hi = joint_range if joint_range is not None else np.nanpercentile(band, pct)
        out[..., i] = np.clip((band - lo) / max(hi - lo, 1e-6), 0, 1) ** gamma
    return np.nan_to_num(out * 255.0).astype("uint8")


def save_webp(rgb: np.ndarray, valid: np.ndarray, path: Path, max_width: int) -> dict:
    alpha = (valid * 255).astype("uint8")
    img = Image.fromarray(np.dstack([rgb, alpha]), "RGBA")
    if valid.all():
        img = img.convert("RGB")  # otherwise keep alpha: pixels with no clear look stay empty
    if img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)), Image.LANCZOS)
    for quality in (82, 75, 68, 60):
        img.save(path, "WEBP", quality=quality, method=6)
        if path.stat().st_size <= MAX_BYTES:
            break
    else:
        raise SystemExit(f"{path.name} is above {MAX_BYTES} bytes even at quality 60")
    return {
        "file": path.name,
        "width": img.width,
        "height": img.height,
        "bytes": path.stat().st_size,
        "webp_quality": quality,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "apps" / "web" / "public" / "imagery")
    parser.add_argument("--max-cloud", type=float, default=5.0)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    area = box(*BBOX)
    items = s2.search(area, *WINDOW, max_cloud=args.max_cloud)
    # Whole acquisition days (all MGRS tiles of one pass), ranked by their worst tile cloud cover;
    # keep days whose tile footprints together cover the box.
    days: dict[str, list] = {}
    for it in items:
        days.setdefault(it.datetime.date().isoformat(), []).append(it)
    ranked = []
    for day, day_items in days.items():
        footprint = unary_union([shape(it.geometry) for it in day_items])
        if footprint.covers(area):
            worst = max(it.properties.get("eo:cloud_cover", 100.0) for it in day_items)
            ranked.append((worst, day, day_items))
    if not ranked:
        raise SystemExit("no acquisition day covers the box")
    ranked.sort(key=lambda r: r[0])
    picked = ranked[:MAX_DAYS]
    chosen = [it for _, _, day_items in picked for it in day_items]
    day_order = [day for _, day, _ in picked]  # least cloudy first
    print(f"{len(items)} scenes on {len(days)} days; using {day_order}: {[i.id for i in chosen]}")

    bands = ("red", "green", "blue", "nir")
    ds = s2.load(chosen, area, bands=bands, resolution=RESOLUTION_M).compute()
    times = [str(t)[:10] for t in ds.time.values]
    order = sorted(range(len(times)), key=lambda i: day_order.index(times[i]))
    comp = first_clear(ds[list(bands)], order)
    epsg = int(ds.attrs["epsg"])
    comp = comp.rio.write_crs(f"EPSG:{epsg}")
    wgs = comp.rio.reproject("EPSG:4326", nodata=np.nan)

    outputs = []
    for name, (rgb_bands, pct, gamma, joint) in PRODUCTS.items():
        for suffix, data, width in (("", comp, 2000), ("-wgs84", wgs, 1600)):
            arr = np.dstack([data[b].values for b in rgb_bands]).astype("float32")
            valid = np.isfinite(arr).all(axis=2)
            rgb = stretch(arr, pct, gamma, joint)
            meta = save_webp(rgb, valid, args.out / f"{name}{suffix}.webp", width)
            meta["crs"] = "EPSG:4326" if suffix else f"EPSG:{epsg}"
            meta["bands"] = {k: s2.BANDS[b] for k, b in zip("RGB", rgb_bands, strict=True)}
            meta["clear_share"] = round(float(valid.mean()), 4)
            if suffix:
                x, y = data.x.values, data.y.values
                rx, ry = abs(float(x[1] - x[0])) / 2, abs(float(y[1] - y[0])) / 2
                # Corner coordinates (pixel edges) in MapLibre order: TL, TR, BR, BL.
                west, east = float(x.min()) - rx, float(x.max()) + rx
                south, north = float(y.min()) - ry, float(y.max()) + ry
                meta["coordinates"] = [[west, north], [east, north], [east, south], [west, south]]
            outputs.append(meta)
            print(f"wrote {meta['file']} ({meta['bytes'] / 1e6:.2f} MB)")

    years = sorted({t[:4] for t in times})
    provenance = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "script": "scripts/render_web_imagery.py",
        "aqua_geo_version": geo_version,
        "source": {
            "collection": "sentinel-2-l2a",
            "provider": s2.PLANETARY_COMPUTER.name,
            "stac_url": s2.PLANETARY_COMPUTER.stac_url,
            "scenes": [
                {
                    "id": it.id,
                    "datetime": it.properties["datetime"],
                    "eo:cloud_cover": it.properties.get("eo:cloud_cover"),
                    "s2:processing_baseline": it.properties.get("s2:processing_baseline"),
                    "boa_offset_subtracted": s2.boa_offset(it, s2.PLANETARY_COMPUTER),
                }
                for it in chosen
            ],
        },
        "area": {"name": "Bhitarkanika, Odisha, India", "bbox_wgs84": list(BBOX)},
        "search_window": [WINDOW[0].isoformat(), WINDOW[1].isoformat()],
        "processing": [
            f"aqua_geo.sentinel2.load at {RESOLUTION_M:g} m in EPSG:{epsg} (bilinear; SCL nearest)",
            "DN -> surface reflectance: (DN - offset) / 10000",
            "SCL mask: no data, saturated, cloud shadow, cloud medium/high, cirrus, snow",
            "composite: first clear look, least-cloudy acquisition day first",
            "display stretch: per-band percentiles (see products), gamma; WebP",
            "-wgs84 variants: rioxarray reproject to EPSG:4326 for MapLibre image sources",
        ],
        "products": {
            name: {
                "bands": [s2.BANDS[b] for b in rgb_bands],
                "percentiles": pct,
                "gamma": gamma,
                "joint_stretch": joint,
            }
            for name, (rgb_bands, pct, gamma, joint) in PRODUCTS.items()
        },
        "outputs": outputs,
        "licence": "Copernicus Sentinel data: free, full and open (EU Regulation 1159/2013)",
        "attribution": f"Contains modified Copernicus Sentinel data {', '.join(years)}",
        "use": "Context imagery for the website only; not an analysis product, not MRV evidence.",
    }
    (args.out / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(provenance["attribution"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
