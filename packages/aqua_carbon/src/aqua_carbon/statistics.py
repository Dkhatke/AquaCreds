"""Sampling statistics for plot inventories (AR-TOOL14 conventions).

Uncertainty U = (standard error of the mean × two-sided Student-t at 90%) / mean × 100.
Only *sampling* uncertainty is quantified, as in AR-TOOL14 (§ "Note": measurement and allometric
model error are not quantified by the tool).
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from scipy import stats

from aqua_carbon.constants import CONFIDENCE, TARGET_PRECISION_PCT, UNCERTAINTY_DISCOUNT_TABLE


def t_value(df: int, confidence: float = CONFIDENCE) -> float:
    """Two-sided Student-t critical value."""
    if df < 1:
        raise ValueError("At least two plots are needed to estimate sampling uncertainty")
    return float(stats.t.ppf(1 - (1 - confidence) / 2, df))


@dataclass(frozen=True)
class Estimate:
    """A mean with its 90% confidence half-width (same unit as the mean)."""

    mean: float
    half_width: float
    n: int
    df: int

    @property
    def uncertainty_pct(self) -> float:
        if self.mean == 0:
            return math.inf if self.half_width > 0 else 0.0
        return abs(self.half_width / self.mean) * 100.0


def stratum_estimate(values: Sequence[float]) -> Estimate:
    n = len(values)
    if n < 2:
        raise ValueError(f"Need at least 2 plots per stratum, got {n}")
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
    se = sd / math.sqrt(n)
    return Estimate(mean=mean, half_width=t_value(n - 1) * se, n=n, df=n - 1)


def stratified_total(
    per_ha: Mapping[str, Sequence[float]], area_ha: Mapping[str, float]
) -> Estimate:
    """Area-weighted total over strata: Σ A_i·x̄_i, SE = sqrt(Σ A_i²·s_i²/n_i).

    Degrees of freedom: Σ(n_i − 1) (simple, slightly liberal; Satterthwaite is a refinement).
    """
    missing = set(per_ha) ^ set(area_ha)
    if missing:
        raise ValueError(f"Strata without both plots and area: {sorted(missing)}")
    total = 0.0
    variance = 0.0
    df = 0
    n = 0
    for name, values in per_ha.items():
        est = stratum_estimate(values)
        se = est.half_width / t_value(est.df)
        total += area_ha[name] * est.mean
        variance += (area_ha[name] * se) ** 2
        df += est.df
        n += est.n
    return Estimate(mean=total, half_width=t_value(df) * math.sqrt(variance), n=n, df=df)


def discount_fraction(uncertainty_pct: float) -> float:
    """AR-TOOL14 Appendix 2 discount, as a fraction of U (not of the mean)."""
    for upper, fraction in UNCERTAINTY_DISCOUNT_TABLE:
        if uncertainty_pct <= upper:
            return fraction
    return 1.0


def conservative_value(est: Estimate, *, side: str) -> float:
    """Discounted mean: lowered for project stocks/removals, raised for baseline values."""
    if side not in {"project", "baseline"}:
        raise ValueError("side must be 'project' or 'baseline'")
    adjustment = discount_fraction(est.uncertainty_pct) * est.half_width
    return est.mean - adjustment if side == "project" else est.mean + adjustment


def required_plots(cv_pct: float, precision_pct: float = TARGET_PRECISION_PCT) -> int:
    """Plots needed for the 90% CI half-width to reach `precision_pct` of the mean, given an
    expected coefficient of variation (iterates because t depends on n)."""
    if cv_pct <= 0 or precision_pct <= 0:
        raise ValueError("CV and precision must be positive")
    # Start from the large-sample (normal) answer, which is a lower bound, then step up until
    # the Student-t requirement is met. Starting at n = 2 overshoots because t(1) ≈ 6.3.
    z = float(stats.norm.ppf(1 - (1 - CONFIDENCE) / 2))
    n = max(2, math.ceil((z * cv_pct / precision_pct) ** 2))
    while math.ceil((t_value(n - 1) * cv_pct / precision_pct) ** 2) > n:
        n += 1
    return n
