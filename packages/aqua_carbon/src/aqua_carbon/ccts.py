"""Methodology profile: India CCTS **BM FR05.001 v1.0** — A/R of degraded mangrove habitats.

Implements, per verification period T = t2 − t1 (BM FR05.001 §4.4–4.8):

    ΔC_P,t      = ΔC_TREE + ΔC_SHRUB + ΔC_DW + ΔSOC                    eq. (3)
    ΔSOC        = 44/12 × Σ A_PLANT × dSOC × 1 yr                       eq. (4)
    ΔC_ACTUAL,t = ΔC_P,t − GHG_E,t                                      eq. (2)
    ΔC_AR,t     = ΔC_ACTUAL,t − ΔC_BSL,t − LK_t                         eq. (6)
    tCCC(t2)    = Σ ΔC_AR from project start to t2                      eq. (7)
    lCCC(t2)    = Σ ΔC_AR over (t1, t2]                                 eq. (8)
    lCCC < 0    → that many lCCCs must be replaced (reversal)           §4.8 ¶23

Tree stocks come from sample plots via AR-TOOL14, made conservative with the AR-TOOL14
uncertainty discount. Shrub and dead-wood pools are not modelled yet (dead wood is optional; shrubs
are rare in planted mangrove — add when a project needs them).
"""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from aqua_carbon.allometry import KOMIYAMA_BGB, Plot, plots_by_stratum
from aqua_carbon.constants import (
    CARBON_FRACTION,
    CO2_PER_C,
    SOC_RATE,
    SOC_YEARS,
    UNCERTAINTY_DISCOUNT_SOURCE,
    Factor,
)
from aqua_carbon.statistics import Estimate, conservative_value, discount_fraction, stratified_total
from aqua_carbon.trace import Trace

METHODOLOGY = "ccts_bm_fr05_001"
METHODOLOGY_SOURCE = "BEE BM FR05.001 v1.0 (27 Mar 2025)"


@dataclass(frozen=True)
class PlantingCohort:
    year: int
    area_ha: float


@dataclass(frozen=True)
class VerificationInput:
    project_start_year: int
    period_start_year: int  # t1: project start or previous verification
    period_end_year: int  # t2: this verification
    tree_biomass_t_dm: Estimate  # total tree biomass (AGB + BGB) over the project area at t2
    previous_tree_stock_tco2e: float = 0.0  # conservative tree stock certified at t1
    include_soc: bool = True
    planting: Sequence[PlantingCohort] = ()
    baseline_removals_tco2e: float = 0.0  # ΔC_BSL over the period (BM-T-AR-0004)
    leakage_tco2e: float = 0.0  # LK over the period (BM-T-AR-0005)
    nonco2_emissions_tco2e: float = 0.0  # GHG_E over the period (BM-T-AR-0002)
    previous_cumulative_removals_tco2e: float = 0.0  # Σ ΔC_AR from project start to t1
    carbon_fraction: Factor = CARBON_FRACTION
    input_warnings: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.project_start_year <= self.period_start_year < self.period_end_year:
            raise ValueError("Require project_start ≤ t1 < t2")
        if self.project_start_year < 2025:
            raise ValueError("CCTS offset projects must start on or after 2025")


@dataclass(frozen=True)
class VerificationResult:
    methodology: str
    period: tuple[int, int]
    tree_stock_tco2e: float
    delta_tree_tco2e: float
    delta_soc_tco2e: float
    net_removals_tco2e: float  # ΔC_AR over the period
    lccc: float  # may be negative (reversal)
    tccc: float
    lccc_issuable_units: int  # whole tCO2e units issuable now
    replacement_required_units: int  # lCCCs to replace because of a reversal
    uncertainty_pct: float
    discount_fraction: float
    trace: Trace

    def to_dict(self) -> dict[str, Any]:
        return {
            "methodology": self.methodology,
            "period": list(self.period),
            "tree_stock_tco2e": round(self.tree_stock_tco2e, 4),
            "delta_tree_tco2e": round(self.delta_tree_tco2e, 4),
            "delta_soc_tco2e": round(self.delta_soc_tco2e, 4),
            "net_removals_tco2e": round(self.net_removals_tco2e, 4),
            "lccc": round(self.lccc, 4),
            "tccc": round(self.tccc, 4),
            "lccc_issuable_units": self.lccc_issuable_units,
            "replacement_required_units": self.replacement_required_units,
            "uncertainty_pct": round(self.uncertainty_pct, 4),
            "discount_fraction": self.discount_fraction,
            "trace": self.trace.to_dict(),
        }


def soc_accrual_tc(planting: Iterable[PlantingCohort], t1: int, t2: int) -> float:
    """tC accrued in years y ∈ (t1, t2]; a cohort planted in year p accrues in years p+1 … p+20."""
    total = 0.0
    for cohort in planting:
        for year in range(t1 + 1, t2 + 1):
            if cohort.year < year <= cohort.year + SOC_YEARS:
                total += cohort.area_ha * SOC_RATE.value
    return total


def tree_biomass_from_plots(
    plots: Iterable[Plot], stratum_area_ha: dict[str, float]
) -> tuple[Estimate, list[str]]:
    """Stratified total tree biomass (t d.m.) from plot measurements, plus any warnings."""
    warnings = []
    if not KOMIYAMA_BGB.verified:
        warnings.append(
            "Below-ground biomass uses Komiyama et al. 2005 coefficients pending full-text "
            "verification"
        )
    return stratified_total(plots_by_stratum(plots), stratum_area_ha), warnings


def compute(inp: VerificationInput) -> VerificationResult:
    tr = Trace()
    for w in inp.input_warnings:
        tr.warn(w)
    est = inp.tree_biomass_t_dm
    t1, t2 = inp.period_start_year, inp.period_end_year

    u_pct = tr.add(
        "uncertainty_pct",
        "Sampling uncertainty of tree biomass (90% CI)",
        est.uncertainty_pct,
        "%",
        "t₀.₉₀ × SE / mean × 100",
        "CDM AR-TOOL14 v04.1",
    )
    frac = discount_fraction(u_pct)
    tr.add(
        "discount_fraction",
        "Uncertainty discount (fraction of U)",
        frac,
        "-",
        "table lookup on U",
        UNCERTAINTY_DISCOUNT_SOURCE,
    )
    tr.add(
        "biomass_mean", "Tree biomass, mean estimate", est.mean, "t d.m.", "Σ A_i·x̄_i", "AR-TOOL14"
    )
    biomass = tr.add(
        "biomass_conservative",
        "Tree biomass, conservative (project side)",
        conservative_value(est, side="project"),
        "t d.m.",
        "mean − discount × half-width",
        UNCERTAINTY_DISCOUNT_SOURCE,
    )
    if not inp.carbon_fraction.verified:
        tr.warn(f"Carbon fraction not verified: {inp.carbon_fraction.source}")
    carbon = tr.add(
        "tree_carbon",
        "Tree carbon stock",
        biomass * inp.carbon_fraction.value,
        "tC",
        f"biomass × CF ({inp.carbon_fraction.value})",
        inp.carbon_fraction.source,
    )
    stock = tr.add(
        "tree_stock_t2",
        "Tree carbon stock at t2",
        carbon * CO2_PER_C.value,
        "tCO2e",
        "tC × 44/12",
        CO2_PER_C.source,
    )
    tr.add(
        "tree_stock_t1",
        "Tree carbon stock at t1 (previously certified)",
        inp.previous_tree_stock_tco2e,
        "tCO2e",
        "input",
        "previous verification",
    )
    d_tree = tr.add(
        "delta_tree",
        "ΔC_TREE over the period",
        stock - inp.previous_tree_stock_tco2e,
        "tCO2e",
        "stock(t2) − stock(t1)",
        METHODOLOGY_SOURCE + " eq. (3)",
    )

    d_soc = 0.0
    if inp.include_soc:
        if not inp.planting:
            tr.warn("SOC pool selected but no planting cohorts were provided; ΔSOC = 0")
        d_soc = tr.add(
            "delta_soc",
            "ΔSOC over the period (default rate)",
            soc_accrual_tc(inp.planting, t1, t2) * CO2_PER_C.value,
            "tCO2e",
            f"44/12 × Σ A_PLANT × {SOC_RATE.value} tC/ha/yr (≤ {SOC_YEARS} yr after planting)",
            SOC_RATE.source,
        )
    d_p = tr.add(
        "delta_c_p",
        "ΔC_P",
        d_tree + d_soc,
        "tCO2e",
        "ΔC_TREE + ΔSOC",
        METHODOLOGY_SOURCE + " eq. (3)",
    )
    d_actual = tr.add(
        "delta_c_actual",
        "ΔC_ACTUAL",
        d_p - inp.nonco2_emissions_tco2e,
        "tCO2e",
        "ΔC_P − GHG_E",
        METHODOLOGY_SOURCE + " eq. (2)",
    )
    d_ar = tr.add(
        "delta_c_ar",
        "Net anthropogenic removals ΔC_AR",
        d_actual - inp.baseline_removals_tco2e - inp.leakage_tco2e,
        "tCO2e",
        "ΔC_ACTUAL − ΔC_BSL − LK",
        METHODOLOGY_SOURCE + " eq. (6)",
    )
    lccc = tr.add(
        "lccc",
        "lCCC for the period",
        d_ar,
        "tCO2e",
        "Σ ΔC_AR over (t1, t2]",
        METHODOLOGY_SOURCE + " eq. (8)",
    )
    tccc = tr.add(
        "tccc",
        "tCCC (cumulative since project start)",
        inp.previous_cumulative_removals_tco2e + d_ar,
        "tCO2e",
        "Σ ΔC_AR since start",
        METHODOLOGY_SOURCE + " eq. (7)",
    )
    issuable = max(0, math.floor(lccc))
    replacement = max(0, math.ceil(-lccc))
    if replacement:
        tr.warn(f"Reversal: {replacement} lCCC must be replaced (BM FR05.001 §4.8 ¶23)")

    return VerificationResult(
        methodology=METHODOLOGY,
        period=(t1, t2),
        tree_stock_tco2e=stock,
        delta_tree_tco2e=d_tree,
        delta_soc_tco2e=d_soc,
        net_removals_tco2e=d_ar,
        lccc=lccc,
        tccc=tccc,
        lccc_issuable_units=issuable,
        replacement_required_units=replacement,
        uncertainty_pct=u_pct,
        discount_fraction=frac,
        trace=tr,
    )
