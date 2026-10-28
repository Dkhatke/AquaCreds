"""Leakage-safe splits for the field-photo verifier dataset (Model C).

Usage:  uv run python ml/pipelines/photos_splits.py   → data/photos_splits.csv + summary JSON

Why not random: one observer's photos share camera, framing, site and often the same plant, so a
random split leaks and inflates accuracy. Instead:

1. **Held-out state:** every Indian observation from `HELDOUT_STATE` (Maharashtra: 2nd-largest
   Indian share of positives; Kerala, the largest, stays in training) goes to `test_state`.
2. Observers who have *any* photo in the held-out state are removed from the other splits
   (`excluded_overlap`), so no observer contributes to both training and the state test.
3. The remaining photos are split **by observer** (all of an observer's photos in one split) using a
   salted SHA-256 of the observer id: ~70% train, ~10% val, ~20% `test_observer`.

The split is a pure function of the manifest, so it is reproducible without a random seed.
"""

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data" / "photos_manifest.csv"
SPLITS = ROOT / "data" / "photos_splits.csv"
SUMMARY = ROOT / "data" / "photos_splits_summary.json"

HELDOUT_STATE = "Maharashtra"
SALT = "aquacreds-photo-verifier-v1"
TRAIN_PCT, VAL_PCT = 70, 10  # the rest (20) is test_observer
SPLIT_NAMES = ("train", "val", "test_observer", "test_state", "excluded_overlap")


def observer_bucket(observer_id: object, salt: str = SALT) -> int:
    """Stable 0-99 bucket for an observer id."""
    digest = hashlib.sha256(f"{salt}:{observer_id}".encode()).hexdigest()
    return int(digest[:8], 16) % 100


def assign_photo_splits(df: pd.DataFrame, heldout_state: str = HELDOUT_STATE) -> pd.DataFrame:
    """Return `df` with a `split` column (see module docstring)."""
    out = df.copy()
    india = out["country_india"].astype(str).str.lower() == "true"
    in_state = india & (out["state"].fillna("") == heldout_state)
    state_observers = set(out.loc[in_state, "observer_id"])
    bucket = out["observer_id"].map(observer_bucket)
    out["split"] = "test_observer"
    out.loc[bucket < TRAIN_PCT + VAL_PCT, "split"] = "val"
    out.loc[bucket < TRAIN_PCT, "split"] = "train"
    out.loc[~in_state & out["observer_id"].isin(state_observers), "split"] = "excluded_overlap"
    out.loc[in_state, "split"] = "test_state"
    return out


def check_no_leakage(df: pd.DataFrame) -> None:
    """Raise if any observer appears in more than one used split."""
    used = df[df["split"] != "excluded_overlap"]
    per_observer = used.groupby("observer_id")["split"].nunique()
    leaking = per_observer[per_observer > 1]
    if len(leaking):
        raise AssertionError(f"{len(leaking)} observers span several splits")


def split_summary(df: pd.DataFrame) -> dict[str, Any]:
    table = df.groupby(["split", "class_key"]).size().unstack(fill_value=0)
    return {
        "heldout_state": HELDOUT_STATE,
        "per_split": {s: int((df["split"] == s).sum()) for s in SPLIT_NAMES},
        "per_split_label": {
            s: {k: int(v) for k, v in g["label"].value_counts().items()}
            for s, g in df.groupby("split")
        },
        "observers_per_split": {s: int(g["observer_id"].nunique()) for s, g in df.groupby("split")},
        "per_split_class": {s: {k: int(v) for k, v in row.items()} for s, row in table.iterrows()},
    }


def main() -> int:
    df = pd.read_csv(MANIFEST, dtype={"state": str})
    df = df[df["sha256"].notna() & (df["sha256"] != "")]  # downloaded images only
    out = assign_photo_splits(df)
    check_no_leakage(out)
    out[["photo_id", "observation_id", "observer_id", "class_key", "label", "split"]].to_csv(
        SPLITS, index=False
    )
    summary = split_summary(out)
    SUMMARY.write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary["per_split"], indent=1))
    print(json.dumps(summary["per_split_label"], indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
