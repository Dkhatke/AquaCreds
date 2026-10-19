import pytest

from aqua_carbon.allometry import Plot, Tree, komiyama_agb_kg, komiyama_bgb_kg
from aqua_carbon.ccts import (
    PlantingCohort,
    VerificationInput,
    compute,
    soc_accrual_tc,
    tree_biomass_from_plots,
)
from aqua_carbon.statistics import Estimate


def test_komiyama_equations() -> None:
    # D = 10 cm, ρ = 0.7 g/cm³
    assert komiyama_agb_kg(10, 0.7) == pytest.approx(0.251 * 0.7 * 10**2.46)
    assert komiyama_agb_kg(10, 0.7) == pytest.approx(50.673, abs=1e-3)
    assert komiyama_bgb_kg(10, 0.7) == pytest.approx(0.199 * 0.7**0.899 * 10**2.22)


def test_plot_scales_to_per_hectare_and_ignores_dead_trees() -> None:
    plot = Plot(
        "P-01",
        "dense",
        area_m2=100.0,
        trees=[
            Tree("Avicennia marina", 10.0, 0.7),
            Tree("Avicennia marina", 12.0, 0.7, alive=False),
        ],
    )
    agb, _ = plot.biomass_t_dm_per_ha()
    assert agb == pytest.approx(komiyama_agb_kg(10, 0.7) / 1000 * 100)  # 100 m² → ×100 per ha


def test_tree_validation() -> None:
    with pytest.raises(ValueError, match="DBH must be positive"):
        Tree("x", 0.0, 0.7)
    with pytest.raises(ValueError, match="not plausible"):
        Tree("x", 5.0, 7.0)  # wood density given in kg/m³ by mistake


def test_soc_accrual_counts_years_after_planting_only() -> None:
    cohorts = [PlantingCohort(2026, 100.0)]
    assert soc_accrual_tc(cohorts, 2026, 2031) == pytest.approx(0.5 * 100 * 5)
    # accrual stops 20 years after planting
    assert soc_accrual_tc(cohorts, 2044, 2050) == pytest.approx(0.5 * 100 * 2)
    assert soc_accrual_tc(cohorts, 2046, 2050) == 0.0
    # a cohort planted later only accrues from the year after its planting
    late = [PlantingCohort(2029, 40.0)]
    assert soc_accrual_tc(late, 2026, 2031) == pytest.approx(0.5 * 40 * 2)


def test_golden_worked_example_from_research_notes() -> None:
    """RESEARCH_NOTES Q4: 100 ha planted 2026, verified 2031, mean tree biomass 25 t d.m./ha
    with U = 14 % → lCCC ≈ 5,074 tCO2e."""
    est = Estimate(mean=25.0 * 100, half_width=0.14 * 2500, n=24, df=23)
    result = compute(
        VerificationInput(
            project_start_year=2026,
            period_start_year=2026,
            period_end_year=2031,
            tree_biomass_t_dm=est,
            planting=[PlantingCohort(2026, 100.0)],
        )
    )
    assert result.discount_fraction == 0.25
    assert result.trace.value("biomass_conservative") == pytest.approx(2412.5)
    assert result.tree_stock_tco2e == pytest.approx(4157.54, abs=0.01)  # 2412.5 × 0.47 × 44/12
    assert result.delta_soc_tco2e == pytest.approx(916.67, abs=0.01)
    assert result.lccc == pytest.approx(5074.21, abs=0.01)
    assert result.tccc == pytest.approx(result.lccc)
    assert result.lccc_issuable_units == 5074
    assert result.replacement_required_units == 0
    assert [s.key for s in result.trace.steps][-2:] == ["lccc", "tccc"]


def test_second_period_uses_previous_stock_and_cumulative_tccc() -> None:
    est = Estimate(mean=6000.0, half_width=300.0, n=30, df=29)  # U = 5 % → no discount
    result = compute(
        VerificationInput(
            project_start_year=2026,
            period_start_year=2031,
            period_end_year=2034,
            tree_biomass_t_dm=est,
            previous_tree_stock_tco2e=4157.58,
            previous_cumulative_removals_tco2e=5074.25,
            planting=[PlantingCohort(2026, 100.0)],
        )
    )
    stock = 6000 * 0.47 * 44 / 12
    assert result.delta_tree_tco2e == pytest.approx(stock - 4157.58)
    assert result.delta_soc_tco2e == pytest.approx(0.5 * 100 * 3 * 44 / 12)
    assert result.tccc == pytest.approx(5074.25 + result.lccc)


def test_reversal_requires_replacement_not_issuance() -> None:
    # Cyclone damage: conservative stock fell below the previously certified stock.
    est = Estimate(mean=1500.0, half_width=150.0, n=20, df=19)
    result = compute(
        VerificationInput(
            project_start_year=2026,
            period_start_year=2031,
            period_end_year=2033,
            tree_biomass_t_dm=est,
            previous_tree_stock_tco2e=4157.58,
            include_soc=False,
        )
    )
    assert result.lccc < 0
    assert result.lccc_issuable_units == 0
    assert result.replacement_required_units == pytest.approx(-result.lccc, abs=1)
    assert any("Reversal" in w for w in result.trace.warnings)


def test_baseline_leakage_and_nonco2_are_deducted() -> None:
    est = Estimate(mean=2500.0, half_width=0.0, n=10, df=9)
    base = {
        "project_start_year": 2026,
        "period_start_year": 2026,
        "period_end_year": 2031,
        "tree_biomass_t_dm": est,
        "include_soc": False,
    }
    gross = compute(VerificationInput(**base)).lccc  # type: ignore[arg-type]
    net = compute(
        VerificationInput(  # type: ignore[arg-type]
            **base, baseline_removals_tco2e=100.0, leakage_tco2e=50.0, nonco2_emissions_tco2e=10.0
        )
    ).lccc
    assert net == pytest.approx(gross - 160.0)


def test_input_validation() -> None:
    est = Estimate(mean=1.0, half_width=0.0, n=2, df=1)
    with pytest.raises(ValueError, match="2025"):
        VerificationInput(2024, 2024, 2030, est)
    with pytest.raises(ValueError, match="t1 < t2"):
        VerificationInput(2026, 2031, 2031, est)


def test_from_plots_end_to_end_with_trace_warning() -> None:
    trees_dense = [Tree("Avicennia marina", d, 0.7) for d in (8, 9, 10, 11)]
    trees_sparse = [Tree("Rhizophora mucronata", d, 0.8) for d in (5, 6)]
    plots = [
        Plot("D1", "dense", 100.0, trees_dense),
        Plot("D2", "dense", 100.0, trees_dense[:3]),
        Plot("S1", "sparse", 100.0, trees_sparse),
        Plot("S2", "sparse", 100.0, trees_sparse[:1]),
    ]
    est, warnings = tree_biomass_from_plots(plots, {"dense": 70.0, "sparse": 30.0})
    result = compute(
        VerificationInput(
            2026, 2026, 2031, est, planting=[PlantingCohort(2026, 100.0)], input_warnings=warnings
        )
    )
    assert result.lccc > 0
    assert any("Komiyama" in w for w in result.trace.warnings)
    payload = result.to_dict()
    assert payload["methodology"] == "ccts_bm_fr05_001"
    assert payload["trace"]["steps"][0]["key"] == "uncertainty_pct"
