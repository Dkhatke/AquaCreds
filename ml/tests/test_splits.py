import numpy as np
import pandas as pd
import pytest
from splits import GULF_OF_KUTCH, assign_splits, cap_per_cell, h3_cells


def _points(n: int = 2000, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    # scatter along India's east coast plus some in the Gulf of Kutch
    lat = np.concatenate([rng.uniform(10, 22, n - 200), rng.uniform(22.3, 23.7, 200)])
    lon = np.concatenate([rng.uniform(79, 89, n - 200), rng.uniform(68.5, 70.5, 200)])
    labels = rng.choice(["mangrove", "water", "terrestrial_other"], size=n)
    return pd.DataFrame({"lat": lat, "lon": lon, "label": labels})


def test_gulf_of_kutch_is_held_out_entirely() -> None:
    df = assign_splits(_points())
    x0, y0, x1, y1 = GULF_OF_KUTCH
    kutch = df[(df.lon.between(x0, x1)) & (df.lat.between(y0, y1))]
    assert len(kutch) == 200
    assert (kutch.split == "heldout_region").all()
    assert (df[df.split == "heldout_region"].index.isin(kutch.index)).all()


def test_no_h3_cell_is_split_across_folds() -> None:
    df = assign_splits(_points(), n_folds=5)
    folds = df[df.split.str.startswith("fold_")]
    assert folds.groupby("h3").split.nunique().max() == 1


def test_folds_are_roughly_balanced_and_deterministic() -> None:
    df = assign_splits(_points(), n_folds=5, seed=7)
    counts = df[df.split.str.startswith("fold_")].split.value_counts()
    assert len(counts) == 5
    assert counts.max() / counts.min() < 1.3
    again = assign_splits(_points(), n_folds=5, seed=7)
    assert (df.split == again.split).all()


def test_cap_per_cell_limits_each_label_in_each_cell() -> None:
    df = _points()
    df["h3"] = h3_cells(df.lat, df.lon)
    capped = cap_per_cell(df, max_per_cell=1)
    assert capped.groupby(["h3", "label"]).size().max() == 1
    assert set(capped.label) == set(df.label)


def test_invalid_fold_count() -> None:
    with pytest.raises(ValueError, match="n_folds"):
        assign_splits(_points(), n_folds=1)
