"""Cloud-free dry-season Sentinel-2 composite + spectral indices for a pilot site.

Usage:  uv run python ml/pipelines/site_preview.py bhitarkanika [--season 2024]
        uv run python ml/pipelines/site_preview.py --list

Writes to data/interim/sites/<site>/:
    s2_dry_<season>.tif   multi-band COG (reflectance bands + indices + clear_count)
    quicklook_<season>.png true colour | NDVI | MVI panels
    stats_<season>.json    zonal statistics + provenance (STAC item ids, offsets, versions)

Memory guard: the expected in-memory size is computed BEFORE loading and the run refuses to
proceed above --max-memory-gb (default 2 GB). Sites are small boxes on purpose.
"""

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import xarray as xr
from shapely.geometry import box

from aqua_geo import __version__ as geo_version
from aqua_geo import sentinel2 as s2
from aqua_geo.composite import median_composite, percentile_composite, zonal_summary
from aqua_geo.indices import add_indices

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "interim" / "sites"


@dataclass(frozen=True)
class Site:
    name: str
    state: str
    bbox: tuple[float, float, float, float]  # lon_min, lat_min, lon_max, lat_max
    note: str


SITES: dict[str, Site] = {
    "bhitarkanika": Site(
        "Bhitarkanika", "Odisha", (86.84, 20.66, 86.89, 20.71), "dense natural mangrove"
    ),
    "pichavaram": Site(
        "Pichavaram", "Tamil Nadu", (79.76, 11.41, 79.80, 11.45), "mangrove + restoration canals"
    ),
    "kori_creek": Site(
        "Kori Creek (Gulf of Kutch)",
        "Gujarat",
        (68.60, 23.55, 68.66, 23.60),
        "arid sparse mangrove; held-out test region",
    ),
    "sundarbans": Site(
        "Sundarbans (Sajnekhali)",
        "West Bengal",
        (88.79, 22.10, 88.84, 22.15),
        "largest Indian mangrove",
    ),
}


def estimate_memory_gb(site: Site, n_scenes: int, n_bands: int, res_m: float = 10.0) -> float:
    lon0, lat0, lon1, lat1 = site.bbox
    width_m = (lon1 - lon0) * 111_320 * np.cos(np.radians((lat0 + lat1) / 2))
    height_m = (lat1 - lat0) * 110_540
    pixels = (width_m / res_m) * (height_m / res_m)
    # raw uint16 + float32 reflectance + indices; ×3 headroom for intermediates
    return float(pixels * n_scenes * (n_bands * 6 + 9 * 4) * 3 / 1e9)


def run(
    site_key: str, season: int, max_memory_gb: float, max_cloud: float, max_scenes: int
) -> Path:
    site = SITES[site_key]
    area = box(*site.bbox)
    start, end = date(season, 11, 1), date(season + 1, 3, 31)  # dry season Nov–Mar
    print(f"[{site_key}] searching Sentinel-2 L2A {start}..{end} (cloud < {max_cloud}%)")
    items = s2.search(area, start, end, max_cloud=max_cloud)
    if not items:
        raise SystemExit("No scenes found")
    if len(items) > max_scenes:
        # A median needs ~10–20 clear looks; keep the least cloudy scenes, then restore time order.
        items = sorted(items, key=lambda it: it.properties.get("eo:cloud_cover", 100.0))[
            :max_scenes
        ]
        items = sorted(items, key=lambda it: it.properties["datetime"])
        print(f"[{site_key}] keeping the {max_scenes} least cloudy scenes")
    need_gb = estimate_memory_gb(site, len(items), len(s2.BANDS))
    print(f"[{site_key}] {len(items)} scenes; estimated peak memory {need_gb:.2f} GB")
    if need_gb > max_memory_gb:
        raise SystemExit(
            f"Refusing: needs ~{need_gb:.1f} GB > limit {max_memory_gb} GB. "
            "Use a smaller area, fewer scenes (--max-cloud), or raise --max-memory-gb."
        )

    ds = s2.load(items, area)
    ds = add_indices(ds)
    comp = median_composite(ds, min_clear=3).compute()
    pct = percentile_composite(ds, ["ndvi", "mvi", "ndwi"]).compute()
    out_ds = xr.merge([comp, pct])

    folder = OUT / site_key
    folder.mkdir(parents=True, exist_ok=True)
    tif = folder / f"s2_dry_{season}.tif"
    epsg = ds.attrs["epsg"]
    stack = out_ds.to_array("band").astype("float32").rio.write_crs(f"EPSG:{epsg}")
    stack.attrs["long_name"] = list(out_ds.data_vars)
    stack.rio.to_raster(tif, driver="COG", compress="DEFLATE")

    stats = {
        "site": site.name,
        "state": site.state,
        "bbox": site.bbox,
        "season": f"{season}-{season + 1} (Nov–Mar)",
        "epsg": epsg,
        "scenes": len(items),
        "item_ids": [it.id for it in items],
        "processing_baselines": sorted(
            {str(it.properties.get("s2:processing_baseline")) for it in items}
        ),
        "clear_count": zonal_summary(comp["clear_count"].astype("float32")),
        "indices": {
            k: zonal_summary(comp[k])
            for k in ("ndvi", "evi", "mvi", "ndwi", "mndwi", "ndmi", "cmri")
        },
        "aqua_geo_version": geo_version,
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (folder / f"stats_{season}.json").write_text(json.dumps(stats, indent=2))
    _quicklook(comp, folder / f"quicklook_{season}.png", f"{site.name}, {stats['season']}")
    print(f"[{site_key}] wrote {tif.relative_to(ROOT)} ({tif.stat().st_size / 1e6:.1f} MB)")
    for k in ("ndvi", "mvi", "ndwi"):
        s = stats["indices"][k]
        print(
            f"  {k:5s} p10={s.get('p10')} p50={s.get('p50')} p90={s.get('p90')} valid={s['valid_fraction']}"
        )
    return tif


def _quicklook(comp: xr.Dataset, path: Path, title: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rgb = np.dstack([comp[b].values for b in ("red", "green", "blue")])
    lo, hi = np.nanpercentile(rgb, [2, 98])
    rgb = np.clip((rgb - lo) / (hi - lo + 1e-9), 0, 1)
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.2))
    axes[0].imshow(np.nan_to_num(rgb))
    axes[0].set_title("True colour (median)")
    im1 = axes[1].imshow(comp["ndvi"].values, cmap="YlGn", vmin=-0.2, vmax=0.9)
    axes[1].set_title("NDVI")
    im2 = axes[2].imshow(comp["mvi"].values, cmap="viridis", vmin=0, vmax=10)
    axes[2].set_title("MVI (Baloloy 2020)")
    for ax in axes:
        ax.set_axis_off()
    fig.colorbar(im1, ax=axes[1], fraction=0.046)
    fig.colorbar(im2, ax=axes[2], fraction=0.046)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("site", nargs="?")
    parser.add_argument("--season", type=int, default=2024, help="dry season starting Nov of YEAR")
    parser.add_argument("--max-memory-gb", type=float, default=2.0)
    parser.add_argument("--max-cloud", type=float, default=60.0)
    parser.add_argument("--max-scenes", type=int, default=20, help="least-cloudy scenes kept")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list or not args.site:
        for key, s in SITES.items():
            print(f"{key:13s} {s.state:12s} {s.bbox}  {s.note}")
        return
    if args.site not in SITES:
        parser.error(f"unknown site {args.site}")
    run(args.site, args.season, args.max_memory_gb, args.max_cloud, args.max_scenes)


if __name__ == "__main__":
    sys.exit(main())
