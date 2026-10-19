"""Spatial train/validation/test splitting for geospatial ML.

Why: neighbouring pixels are nearly identical, so a random split leaks information and inflates
accuracy (the SIH prototype's failure mode). Instead:

1. A whole geographic region (Gulf of Kutch) is held out as an *unseen coast* test set.
2. Remaining points are grouped into H3 cells (res 5 ≈ 250 km²) and whole cells are assigned to
   cross-validation folds, so no cell contributes to both training and validation.
3. Points per (cell, label) are capped so dense regions (e.g. the Sundarbans) don't dominate.
"""

from collections.abc import Sequence

import h3
import numpy as np
import pandas as pd

# lon_min, lat_min, lon_max, lat_max
GULF_OF_KUTCH = (68.0, 22.0, 70.8, 24.0)
H3_RES = 5


def h3_cells(lat: Sequence[float], lon: Sequence[float], res: int = H3_RES) -> list[str]:
    return [h3.latlng_to_cell(float(a), float(o), res) for a, o in zip(lat, lon, strict=True)]


def in_bbox(lat: pd.Series, lon: pd.Series, bbox: tuple[float, float, float, float]) -> pd.Series:
    x0, y0, x1, y1 = bbox
    return (lon >= x0) & (lon <= x1) & (lat >= y0) & (lat <= y1)


def cap_per_cell(df: pd.DataFrame, max_per_cell: int, seed: int = 0) -> pd.DataFrame:
    """Keep at most `max_per_cell` points per (h3 cell, label), sampled reproducibly."""
    shuffled = df.sample(frac=1.0, random_state=seed)
    keep = shuffled.groupby(["h3", "label"]).cumcount() < max_per_cell
    return shuffled[keep].reset_index(drop=True)


def assign_splits(
    df: pd.DataFrame,
    *,
    n_folds: int = 5,
    heldout_bbox: tuple[float, float, float, float] | None = GULF_OF_KUTCH,
    seed: int = 0,
) -> pd.DataFrame:
    """Add `h3` and `split` columns. `split` ∈ {"heldout_region", "fold_0", …}.

    Cells are assigned greedily (largest first) to the fold with the fewest points so far, which
    keeps folds balanced in size while never splitting a cell.
    """
    if n_folds < 2:
        raise ValueError("n_folds must be ≥ 2")
    out = df.copy()
    out["h3"] = h3_cells(out["lat"], out["lon"])
    out["split"] = ""
    if heldout_bbox is not None:
        out.loc[in_bbox(out["lat"], out["lon"], heldout_bbox), "split"] = "heldout_region"

    pool = out[out["split"] == ""]
    sizes = pool.groupby("h3").size()
    rng = np.random.default_rng(seed)
    cells = sizes.index.to_numpy()
    rng.shuffle(cells)  # random tie-breaking, then sort by size (stable)
    cells = sorted(cells, key=lambda c: -sizes[c])
    load = np.zeros(n_folds, dtype=int)
    fold_of: dict[str, int] = {}
    for cell in cells:
        target = int(np.argmin(load))
        fold_of[cell] = target
        load[target] += int(sizes[cell])
    mask = out["split"] == ""
    out.loc[mask, "split"] = out.loc[mask, "h3"].map(lambda c: f"fold_{fold_of[c]}")
    return out
