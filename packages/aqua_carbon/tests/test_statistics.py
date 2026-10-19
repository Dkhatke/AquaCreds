import math

import pytest

from aqua_carbon.statistics import (
    Estimate,
    conservative_value,
    discount_fraction,
    required_plots,
    stratified_total,
    stratum_estimate,
    t_value,
)


def test_t_value_matches_ar_tool14_worked_example() -> None:
    # AR-TOOL14 v04.1 §Definitions example: t(0.1, 33) = 1.692
    assert t_value(33) == pytest.approx(1.692, abs=5e-4)


def test_uncertainty_matches_ar_tool14_worked_example() -> None:
    # Example in the tool: SE = 2.191, mean = 45.328 t d.m./ha, df = 33 → U ≈ 8.18 %
    est = Estimate(mean=45.328, half_width=2.191 * t_value(33), n=34, df=33)
    assert est.uncertainty_pct == pytest.approx(8.18, abs=0.01)


@pytest.mark.parametrize(
    ("u", "fraction"),
    [
        (0, 0.0),
        (10.0, 0.0),
        (10.01, 0.25),
        (15.0, 0.25),
        (15.5, 0.5),
        (20.0, 0.5),
        (25.0, 0.75),
        (30.0, 0.75),
        (30.1, 1.0),
        (math.inf, 1.0),
    ],
)
def test_discount_table_boundaries(u: float, fraction: float) -> None:
    assert discount_fraction(u) == fraction


def test_conservative_value_direction() -> None:
    # U = 15 % (half-width 9 on mean 60) → discount 25 % of U = 2.25  (AR-TOOL14 App. 2 example)
    est = Estimate(mean=60.0, half_width=9.0, n=10, df=9)
    assert conservative_value(est, side="project") == pytest.approx(57.75)
    assert conservative_value(est, side="baseline") == pytest.approx(62.25)
    with pytest.raises(ValueError, match="side must be"):
        conservative_value(est, side="other")


def test_stratum_estimate() -> None:
    est = stratum_estimate([20.0, 24.0, 26.0, 30.0])
    assert est.mean == pytest.approx(25.0)
    sd = math.sqrt(((5**2) + 1 + 1 + 25) / 3)
    assert est.half_width == pytest.approx(t_value(3) * sd / 2)
    with pytest.raises(ValueError, match="at least 2"):
        stratum_estimate([10.0])


def test_stratified_total_weights_by_area() -> None:
    per_ha = {"dense": [30.0, 34.0, 32.0], "sparse": [10.0, 12.0, 11.0]}
    est = stratified_total(per_ha, {"dense": 60.0, "sparse": 40.0})
    assert est.mean == pytest.approx(60 * 32 + 40 * 11)
    assert est.df == 4
    with pytest.raises(ValueError, match="Strata"):
        stratified_total(per_ha, {"dense": 60.0})


def test_required_plots_reaches_target_precision() -> None:
    n = required_plots(cv_pct=30.0, precision_pct=10.0)
    assert n == 27
    assert t_value(n - 1) * 30.0 / math.sqrt(n) <= 10.0  # target met...
    assert t_value(n - 2) * 30.0 / math.sqrt(n - 1) > 10.0  # ...and n is the minimum
    assert required_plots(cv_pct=5.0) <= 5
    assert required_plots(cv_pct=60.0) > required_plots(cv_pct=30.0)
