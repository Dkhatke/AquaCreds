import numpy as np
import pytest
from classifier_eval import (
    Candidate,
    confusion_matrix,
    mean_std,
    per_class,
    report,
    select_winner,
)

CLASSES = ["mangrove", "water", "tidal_flat"]


def test_confusion_matrix_rows_are_reference() -> None:
    cm = confusion_matrix([0, 0, 1, 2], [0, 1, 1, 1], 3)
    assert cm.tolist() == [[1, 1, 0], [0, 1, 0], [0, 1, 0]]
    with pytest.raises(ValueError, match="length"):
        confusion_matrix([0], [0, 1], 3)
    with pytest.raises(ValueError, match="range"):
        confusion_matrix([3], [0], 3)


def test_per_class_handles_empty_columns() -> None:
    pc = per_class(np.array([[2, 0], [1, 0]]))
    assert pc["precision"].tolist() == pytest.approx([2 / 3, 0.0])
    assert pc["recall"].tolist() == pytest.approx([1.0, 0.0])
    assert pc["f1"][1] == 0.0


def test_report_matches_hand_computation() -> None:
    y_true = [0, 0, 0, 1, 1, 2]
    y_pred = [0, 0, 1, 1, 1, 1]
    r = report(y_true, y_pred, CLASSES, positive="mangrove")
    assert r["n"] == 6
    assert r["accuracy"] == pytest.approx(4 / 6)
    # mangrove: P=1, R=2/3 → F1=0.8; water: P=2/4, R=1 → F1=2/3; tidal_flat: 0
    assert r["per_class"]["mangrove"]["f1"] == pytest.approx(0.8)
    assert r["per_class"]["water"]["f1"] == pytest.approx(0.6667, abs=1e-4)
    assert r["macro_f1"] == pytest.approx((0.8 + 2 / 3 + 0) / 3)
    assert r["mangrove_vs_rest_f1"] == pytest.approx(0.8)
    assert r["confusion_matrix"][0] == [2, 1, 0]


def test_macro_f1_ignores_classes_absent_from_reference() -> None:
    r = report([0, 1], [0, 1], CLASSES, positive="mangrove")
    assert r["macro_f1"] == pytest.approx(1.0)
    assert r["per_class"]["tidal_flat"]["support"] == 0


def test_mean_std() -> None:
    assert mean_std([0.5, 0.7]) == {"mean": 0.6, "std": 0.1}


def test_selection_rule_heldout_then_cv_then_simplicity() -> None:
    rf = Candidate("random_forest", 0.700, 0.80, complexity=1)
    lgbm = Candidate("lightgbm", 0.720, 0.78, complexity=2)
    assert select_winner([rf, lgbm]).name == "lightgbm"
    # held-out tie (within 0.01) → CV decides
    lgbm_tie = Candidate("lightgbm", 0.703, 0.78, complexity=2)
    assert select_winner([rf, lgbm_tie]).name == "random_forest"
    # held-out and CV tie → simpler model
    lgbm_tie2 = Candidate("lightgbm", 0.702, 0.801, complexity=2)
    assert select_winner([lgbm_tie2, rf]).name == "random_forest"
    with pytest.raises(ValueError, match="no candidates"):
        select_winner([])
