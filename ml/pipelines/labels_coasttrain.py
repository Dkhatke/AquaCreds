"""Extract coastTrain labels for the Indian / South Asian coastal window.

Usage:  uv run python ml/pipelines/labels_coasttrain.py

Reads ONLY the AOI window from the zipped GeoJSON (no full extraction, low memory), maps
coastTrain classes to AquaCreds classes, keeps provenance, and writes
data/interim/labels/coasttrain_aoi.parquet.

Known limitations (see docs/DATASETS.md §2, "coastTrain findings"):
* ~95% of mangrove points were *sampled from the 1996 GMW map* (Method=Sampled, Scale=25) rather
  than field-verified, so they must be re-checked against GMW 2020 before training
  (next step: labels_build.py).
* Non-mangrove classes are scarce; negatives are added from ESA WorldCover.
"""

from pathlib import Path

import pyogrio

ROOT = Path(__file__).resolve().parents[2]
SRC = (
    "/vsizip/"
    + str(ROOT / "data/raw/coasttrain/coastTrain-v1.0.0.zip")
    + "/nick-murray-coastTrain-b8f5600/data/coastTrain_v1_0.geojson"
)
OUT = ROOT / "data" / "interim" / "labels" / "coasttrain_aoi.parquet"

# lon_min, lat_min, lon_max, lat_max: India's coasts incl. Andaman & Nicobar + neighbours
AOI_BBOX = (68.0, 6.0, 97.5, 24.8)

CLASS_MAP = {
    "Mangrove": "mangrove",
    "Saltmarsh / Tidal Marsh": "salt_marsh",
    "Tidal Flat": "tidal_flat",
    "Permanent Water": "water",
    "Terrestrial Other": "terrestrial_other",
    "Seagrass": "seagrass",  # research class only (turbid water; see IMPLEMENTATION_PLAN §6.6)
    # "Photic Coral Reef" intentionally dropped: outside AquaCreds' scope.
}


def main() -> None:
    gdf = pyogrio.read_dataframe(SRC, bbox=AOI_BBOX)
    gdf = gdf[gdf["Ecosys_Typ"].isin(CLASS_MAP)].copy()
    gdf["label"] = gdf["Ecosys_Typ"].map(CLASS_MAP)
    gdf["source"] = "coastTrain_v1.0"
    gdf["ref_year"] = gdf["RefDatSta"].astype(str).str[:4].astype(int)
    gdf["map_derived"] = gdf["Method"].eq("Sampled")
    gdf = gdf.rename(columns={"ID": "source_id", "Method": "method", "Scale": "scale_m"})
    gdf = gdf[
        ["source_id", "label", "source", "ref_year", "method", "map_derived", "scale_m", "geometry"]
    ]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(OUT)
    print(f"wrote {OUT.relative_to(ROOT)}: {len(gdf)} points")
    summary = gdf.groupby(["label", "map_derived"]).size().unstack(fill_value=0)
    summary.columns = ["field/interpreted", "map-derived"][: len(summary.columns)]
    print(summary.to_string())


if __name__ == "__main__":
    main()
