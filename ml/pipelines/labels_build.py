"""Build the training label set: coastTrain (re-checked against GMW 2020) + WorldCover negatives,
with spatial splits and per-cell caps.

Usage:  uv run python ml/pipelines/labels_build.py [--negatives-per-cell 40] [--cap 60]

Inputs : data/interim/labels/coasttrain_aoi.parquet   (labels_coasttrain.py)
         data/raw/gmw_v4/gmw_mng_2020_v4019_gtiff.zip  (GMW 2020 mangrove extent tiles)
         ESA WorldCover 2021 via Planetary Computer STAC (point reads only, no bulk download)
Output : data/processed/labels.parquet + data/processed/labels_summary.json

Memory: only point coordinates are held in memory; rasters are sampled per point from COG/zip
tiles (block reads), never loaded whole.
"""

import argparse
import json
import math
import zipfile
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import rasterio
from rasterio.errors import RasterioIOError
from splits import assign_splits, cap_per_cell

from aqua_geo.network import STAC_TIMEOUT_S, rasterio_env

ROOT = Path(__file__).resolve().parents[2]
COASTTRAIN = ROOT / "data/interim/labels/coasttrain_aoi.parquet"
GMW_ZIP = ROOT / "data/raw/gmw_v4/gmw_mng_2020_v4019_gtiff.zip"
OUT = ROOT / "data/processed/labels.parquet"

# ESA WorldCover v200 (2021) class → AquaCreds negative label. 95 (mangroves) is excluded because
# positives come from GMW; 90 (herbaceous wetland) is excluded as ambiguous with salt marsh.
WORLDCOVER_MAP = {
    10: "terrestrial_other",  # tree cover
    20: "terrestrial_other",  # shrubland
    30: "terrestrial_other",  # grassland
    40: "cropland_aquaculture",  # cropland
    50: "built_bare",  # built-up
    60: "built_bare",  # bare / sparse vegetation
    80: "water",  # permanent water bodies
}


def gmw_tile_name(lon: float, lat: float) -> str:
    """GMW v4 tiles are 1°×1°, named by their top-left corner, e.g. GMW_N21E086 = 20–21°N, 86–87°E."""
    top = math.floor(lat) + 1
    left = math.floor(lon)
    ns = "N" if top >= 0 else "S"
    ew = "E" if left >= 0 else "W"
    return f"GMW_{ns}{abs(top):02d}{ew}{abs(left):03d}_v4019_mng.tif"


class GmwIndex:
    """Point lookup into the GMW v4 tile zip: tile chosen by name, single pixels read per point."""

    def __init__(self, zip_path: Path) -> None:
        if not zip_path.exists():
            raise SystemExit(f"Missing {zip_path}; run `uv run dvc repro download_gmw_v4_2020`")
        with zipfile.ZipFile(zip_path) as zf:
            self.members = {
                Path(m).name: m for m in zf.namelist() if m.lower().endswith((".tif", ".tiff"))
            }
        self.zip_path = zip_path
        print(f"GMW v4: {len(self.members)} tiles available")

    def is_mangrove(self, lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
        """True where GMW 2020 marks mangrove. A point whose tile does not exist lies outside the
        mangrove range entirely, so it is False."""
        result = np.zeros(len(lons), dtype=bool)
        by_tile: dict[str, list[int]] = defaultdict(list)
        for i, (x, y) in enumerate(zip(lons, lats, strict=True)):
            name = gmw_tile_name(float(x), float(y))
            if name in self.members:
                by_tile[name].append(i)
        for name, idx in by_tile.items():
            with rasterio.open(f"/vsizip/{self.zip_path}/{self.members[name]}") as ds:
                values = [v[0] for v in ds.sample([(lons[i], lats[i]) for i in idx])]
            result[idx] = np.asarray(values) == 1
        return result


def worldcover_negatives(cells: list[str], per_cell: int, seed: int) -> pd.DataFrame:
    """Random points inside each H3 cell, labelled from ESA WorldCover 2021 (point reads)."""
    import planetary_computer
    from pystac_client import Client

    rng = np.random.default_rng(seed)
    pts: list[tuple[float, float, str]] = []
    for cell in cells:
        lat_c, lon_c = h3.cell_to_latlng(cell)
        edge_deg = h3.average_hexagon_edge_length(h3.get_resolution(cell), unit="km") / 111.0
        for _ in range(per_cell):
            pts.append(
                (
                    lon_c + rng.uniform(-edge_deg, edge_deg),
                    lat_c + rng.uniform(-edge_deg, edge_deg),
                    cell,
                )
            )
    df = pd.DataFrame(pts, columns=["lon", "lat", "cell"])

    catalog = Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=planetary_computer.sign_inplace,
        timeout=STAC_TIMEOUT_S,
    )
    lon0, lat0, lon1, lat1 = df.lon.min(), df.lat.min(), df.lon.max(), df.lat.max()
    items = list(
        catalog.search(
            collections=["esa-worldcover"],
            bbox=[lon0, lat0, lon1, lat1],
            query={"esa_worldcover:product_version": {"eq": "2.0.0"}},
        ).items()
    )
    print(f"WorldCover tiles: {len(items)} for {len(df)} candidate points", flush=True)
    values = np.full(len(df), -1, dtype=int)
    for n, item in enumerate(items, 1):
        x0, y0, x1, y1 = item.bbox
        inside = np.where(
            (df.lon >= x0) & (df.lon < x1) & (df.lat > y0) & (df.lat <= y1) & (values < 0)
        )[0]
        if len(inside) == 0:
            continue
        try:
            with rasterio_env(), rasterio.open(item.assets["map"].href) as ds:
                sampled = [
                    v[0]
                    for v in ds.sample(
                        list(zip(df.lon.iloc[inside], df.lat.iloc[inside], strict=True))
                    )
                ]
        except RasterioIOError as exc:
            raise SystemExit(f"WorldCover read failed for {item.id}: {exc}") from exc
        values[inside] = sampled
        print(f"  [{n}/{len(items)}] {item.id}: {len(inside)} points", flush=True)
    df["worldcover"] = values
    df["label"] = df.worldcover.map(WORLDCOVER_MAP)
    df = df.dropna(subset=["label"]).copy()
    df["source"] = "esa_worldcover_2021"
    df["ref_year"] = 2021
    df["map_derived"] = True
    return df[["lon", "lat", "label", "source", "ref_year", "map_derived"]]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--negatives-per-cell", type=int, default=40)
    parser.add_argument("--cap", type=int, default=60, help="max points per (H3 cell, label)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    ct = gpd.read_parquet(COASTTRAIN)
    ct["lon"] = ct.geometry.x
    ct["lat"] = ct.geometry.y

    gmw = GmwIndex(GMW_ZIP)
    is_mg = gmw.is_mangrove(ct.lon.to_numpy(), ct.lat.to_numpy())
    stale = (ct.label == "mangrove") & ct.map_derived & ~is_mg
    conflict = (ct.label != "mangrove") & is_mg  # e.g. "water" points GMW now maps as mangrove
    print(
        f"coastTrain: dropping {stale.sum()} stale 1996 mangrove points and {conflict.sum()} conflicting negatives"
    )
    ct = ct[~stale & ~conflict]
    ct = ct[ct.label != "seagrass"]  # research class only (not trained in v2.0)

    cells = sorted(
        {
            h3.latlng_to_cell(a, o, 5)
            for a, o in zip(
                ct[ct.label == "mangrove"].lat, ct[ct.label == "mangrove"].lon, strict=True
            )
        }
    )
    neg = worldcover_negatives(cells, args.negatives_per_cell, args.seed)
    neg = neg[~gmw.is_mangrove(neg.lon.to_numpy(), neg.lat.to_numpy())]

    cols = ["lon", "lat", "label", "source", "ref_year", "map_derived"]
    labels = pd.concat([ct[cols], neg[cols]], ignore_index=True)
    labels = assign_splits(labels, seed=args.seed)
    labels = cap_per_cell(labels, args.cap, seed=args.seed)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    labels.to_parquet(OUT)
    summary = {
        "total": len(labels),
        "by_label": labels.label.value_counts().to_dict(),
        "by_split": labels.split.value_counts().to_dict(),
        "by_label_and_split": labels.groupby(["label", "split"])
        .size()
        .unstack(fill_value=0)
        .to_dict(),
        "params": vars(args),
    }
    (OUT.parent / "labels_summary.json").write_text(json.dumps(summary, indent=2, default=int))
    print(
        json.dumps(
            {k: summary[k] for k in ("total", "by_label", "by_split")}, indent=2, default=int
        )
    )


if __name__ == "__main__":
    main()
