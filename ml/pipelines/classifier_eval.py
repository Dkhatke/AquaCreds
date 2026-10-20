"""Evaluation helpers for the ecosystem classifier (numpy only, so they are testable in CI).

Metrics: confusion matrix, per-class precision/recall/F1, macro-F1, overall accuracy, binary
mangrove-vs-rest F1, plus the model-selection rule from IMPLEMENTATION_PLAN §1d / TECH_STACK §5.2.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

# Differences smaller than this (in macro-F1) are treated as ties by the selection rule. With
# ~1,100 held-out points the sampling error of macro-F1 is about ±0.01-0.02, so finer
# distinctions are noise.
TIE_DECIMALS = 2


def confusion_matrix(y_true: Sequence[int], y_pred: Sequence[int], n_classes: int) -> np.ndarray:
    """Rows = reference class, columns = predicted class."""
    t = np.asarray(y_true, dtype=int)
    p = np.asarray(y_pred, dtype=int)
    if t.shape != p.shape:
        raise ValueError("y_true and y_pred differ in length")
    if t.size and (min(t.min(), p.min()) < 0 or max(t.max(), p.max()) >= n_classes):
        raise ValueError("class index out of range")
    cm = np.zeros((n_classes, n_classes), dtype=int)
    np.add.at(cm, (t, p), 1)
    return cm


def _safe_div(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    out = np.zeros_like(num, dtype=float)
    np.divide(num, den, out=out, where=den > 0)
    return out


def per_class(cm: np.ndarray) -> dict[str, np.ndarray]:
    tp = np.diag(cm).astype(float)
    precision = _safe_div(tp, cm.sum(axis=0).astype(float))
    recall = _safe_div(tp, cm.sum(axis=1).astype(float))
    f1 = _safe_div(2 * precision * recall, precision + recall)
    return {"precision": precision, "recall": recall, "f1": f1, "support": cm.sum(axis=1)}


def report(
    y_true: Sequence[int], y_pred: Sequence[int], classes: Sequence[str], positive: str
) -> dict[str, Any]:
    """Full metric set. Macro-F1 averages over classes *present in y_true* (a class absent from
    the reference set has undefined recall and would otherwise count as F1 = 0)."""
    cm = confusion_matrix(y_true, y_pred, len(classes))
    pc = per_class(cm)
    present = pc["support"] > 0
    pos = list(classes).index(positive)
    t = np.asarray(y_true) == pos
    p = np.asarray(y_pred) == pos
    bin_cm = confusion_matrix(t.astype(int), p.astype(int), 2)
    return {
        "n": int(cm.sum()),
        "accuracy": float(np.trace(cm) / cm.sum()) if cm.sum() else 0.0,
        "macro_f1": float(pc["f1"][present].mean()) if present.any() else 0.0,
        f"{positive}_vs_rest_f1": float(per_class(bin_cm)["f1"][1]),
        "per_class": {
            c: {
                "precision": round(float(pc["precision"][i]), 4),
                "recall": round(float(pc["recall"][i]), 4),
                "f1": round(float(pc["f1"][i]), 4),
                "support": int(pc["support"][i]),
            }
            for i, c in enumerate(classes)
        },
        "confusion_matrix": cm.tolist(),
    }


def mean_std(values: Sequence[float]) -> dict[str, float]:
    arr = np.asarray(values, dtype=float)
    return {"mean": round(float(arr.mean()), 4), "std": round(float(arr.std(ddof=0)), 4)}


@dataclass(frozen=True)
class Candidate:
    name: str
    heldout_macro_f1: float
    cv_macro_f1: float
    complexity: int  # lower = simpler/faster


def select_winner(candidates: Sequence[Candidate]) -> Candidate:
    """Highest held-out macro-F1, then CV macro-F1 (both rounded to TIE_DECIMALS), then the
    simplest model."""
    if not candidates:
        raise ValueError("no candidates")
    return min(
        candidates,
        key=lambda c: (
            -round(c.heldout_macro_f1, TIE_DECIMALS),
            -round(c.cv_macro_f1, TIE_DECIMALS),
            c.complexity,
            c.name,
        ),
    )
