import copy
import hashlib
import json
import math
from typing import Any

import pytest
from pydantic import ValidationError
from scipy import stats

from aqua_mrv.models import ReportInputs
from aqua_mrv.report import STATUS_LABEL, build_report, canonical_json, report_hash
from aqua_mrv.wood_density import UnknownSpeciesError

RHO_AVICENNIA_MARINA = 0.6478  # GWDD mean of 4 records (see data/reference)

# One stratum, 6 plots of 100 m², all Avicennia marina (illustrative DBHs, cm).
GOLDEN_DBH = (
    (10, 9, 9, 8, 7),
    (11, 10, 8, 8),
    (12, 9, 8, 7, 6),
    (9, 8, 7, 7),
    (10, 10, 9, 6),
    (11, 9, 8, 8, 8),
)


def golden_doc() -> dict[str, Any]:
    return {
        "project": {
            "id": "golden",
            "name": "Worked example (RESEARCH_NOTES Q4)",
            "state": "Odisha",
            "area_ha": 100.0,
            "start_year": 2026,
        },
        "period": {"kind": "verification", "start_year": 2026, "end_year": 2031},
        "strata_area_ha": {"planted": 100.0},
        "plots": [
            {
                "code": f"P{i + 1}",
                "stratum": "planted",
                "area_m2": 100.0,
                "trees": [{"species": "Avicennia marina", "dbh_cm": d} for d in dbhs],
            }
            for i, dbhs in enumerate(GOLDEN_DBH)
        ],
        "planting": [{"year": 2026, "area_ha": 100.0}],
        "generated_at": "2031-06-30T00:00:00Z",
    }


def build(doc: dict[str, Any]) -> Any:
    return build_report(ReportInputs.model_validate(doc))


def test_golden_one_plot_hand_check_against_komiyama() -> None:
    doc = golden_doc()
    doc["plots"] = [
        {"code": "H1", "stratum": "planted", "area_m2": 100.0,
         "trees": [{"species": "Avicennia marina", "dbh_cm": 10.0},
                   {"species": "Avicennia marina", "dbh_cm": 8.0},
                   {"species": "Avicennia marina", "dbh_cm": 12.0, "alive": False}]},
        {"code": "H2", "stratum": "planted", "area_m2": 100.0,
         "trees": [{"species": "Avicennia marina", "dbh_cm": 9.0}]},
    ]  # fmt: skip
    plot = build(doc).record["plots"][0]
    # By hand: AGB = 0.251·ρ·D^2.46, BGB = 0.199·ρ^0.899·D^2.22 with ρ = 0.6478
    #   D=10: AGB 46.8937 kg, BGB 22.3531 kg;  D=8: AGB 27.0842 kg, BGB 13.6207 kg
    assert plot["trees"][0]["wood_density_g_cm3"] == RHO_AVICENNIA_MARINA
    assert plot["trees"][0]["agb_kg"] == pytest.approx(46.8937, abs=1e-4)
    assert plot["trees"][0]["bgb_kg"] == pytest.approx(22.3531, abs=1e-4)
    assert plot["trees"][2]["agb_kg"] == 0.0  # dead tree excluded
    # (46.8937 + 27.0842) kg per 100 m² → × 100 / 1000 = 7.39779 t d.m./ha
    assert plot["agb_t_dm_ha"] == pytest.approx(7.39779, abs=1e-5)
    assert plot["bgb_t_dm_ha"] == pytest.approx(3.59738, abs=1e-5)
    assert plot["total_t_dm_ha"] == pytest.approx(10.99517, abs=1e-5)
    assert (plot["live_trees"], plot["dead_trees"]) == (2, 1)


def test_golden_worked_example_end_to_end_through_plots() -> None:
    """RESEARCH_NOTES Q4 chain, recomputed independently: plots → per-ha biomass → mean ± 90% CI
    → AR-TOOL14 discount → × 0.47 × 44/12 → + default SOC (916.67) → lCCC."""
    report = build(golden_doc())
    rho = RHO_AVICENNIA_MARINA

    def tree_kg(d: float) -> float:
        return 0.251 * rho * d**2.46 + 0.199 * rho**0.899 * d**2.22

    per_ha = [sum(tree_kg(d) for d in dbhs) / 1000 * 100 for dbhs in GOLDEN_DBH]
    n = len(per_ha)
    mean = sum(per_ha) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in per_ha) / (n - 1))
    half = float(stats.t.ppf(0.95, n - 1)) * sd / math.sqrt(n)
    u = half / mean * 100
    assert 10 < u <= 15  # same discount band as the worked example (U = 14 %)
    total_mean, total_half = mean * 100, half * 100
    conservative = total_mean - 0.25 * total_half
    tree_stock = conservative * 0.47 * 44 / 12
    soc = 0.5 * 100 * 5 * 44 / 12
    assert soc == pytest.approx(916.67, abs=0.01)

    rec = report.record
    assert rec["biomass_estimate"]["mean_t_dm"] == pytest.approx(total_mean, rel=1e-9)
    assert rec["result"]["uncertainty_pct"] == pytest.approx(u, abs=1e-4)
    assert rec["result"]["discount_fraction"] == 0.25
    assert rec["result"]["tree_stock_tco2e"] == pytest.approx(tree_stock, abs=1e-3)
    assert rec["result"]["delta_soc_tco2e"] == pytest.approx(soc, abs=1e-3)
    assert rec["result"]["lccc"] == pytest.approx(tree_stock + soc, abs=1e-3)
    assert report.lccc_units == math.floor(tree_stock + soc)
    assert [s["key"] for s in rec["trace"]][-2:] == ["lccc", "tccc"]
    assert rec["status"] == STATUS_LABEL
    assert rec["software"]["aqua_carbon"]
    assert rec["software"]["aqua_mrv"]
    assert rec["methodology"]["id"] == "ccts_bm_fr05_001"
    assert rec["wood_density"]["Avicennia marina"]["n"] == 4


def _reverse_keys(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _reverse_keys(obj[k]) for k in reversed(list(obj))}
    if isinstance(obj, list):
        return [_reverse_keys(x) for x in obj]
    return obj


def test_hash_ignores_key_order_but_tracks_values(example_doc: dict[str, Any]) -> None:
    a = build(example_doc)
    shuffled = _reverse_keys(example_doc)
    assert list(shuffled) != list(example_doc)
    b = build(shuffled)
    assert a.report_hash == b.report_hash
    assert canonical_json(a) == canonical_json(b)

    changed = copy.deepcopy(example_doc)
    changed["plots"][0]["trees"][0]["dbh_cm"] += 0.1
    assert build(changed).report_hash != a.report_hash

    restamped = copy.deepcopy(example_doc)
    restamped["generated_at"] = "2031-07-01T00:00:00Z"
    assert build(restamped).report_hash != a.report_hash


def test_hash_is_sha256_of_rfc8785_bytes_and_rebuild_is_reproducible(
    example_doc: dict[str, Any],
) -> None:
    report = build(example_doc)
    raw = report.canonical_json()
    assert report.report_hash == hashlib.sha256(raw).hexdigest() == report_hash(report.record)
    assert report.report_hash_bytes32() == bytes.fromhex(report.report_hash)
    assert len(report.report_hash_bytes32()) == 32
    assert json.loads(raw) == report.record
    assert raw.startswith(b'{"biomass_estimate":{"df":')  # keys sorted, no whitespace
    assert b"\n" not in raw
    assert build(example_doc).report_hash == report.report_hash  # no volatile fields


def test_example_report_contents(example_doc: dict[str, Any]) -> None:
    report = build(example_doc)
    rec = report.record
    assert report.lccc_units == rec["result"]["lccc_issuable_units"] > 0
    assert rec["evidence"]["rejected"] == 17
    assert rec["satellite"]["provenance"]
    assert "ILLUSTRATIVE" in rec["notes"]
    assert any("Komiyama" in w for w in report.warnings)


def test_genus_fallback_is_reported_in_warnings(example_doc: dict[str, Any]) -> None:
    report = build(example_doc)
    assert any("Avicennia sp." in w and "genus mean" in w for w in report.warnings)
    assert report.record["wood_density"]["Avicennia sp."]["genus_fallback"] is True
    assert report.record["wood_density"]["Avicennia marina"]["genus_fallback"] is False


def test_unknown_species_is_a_clear_error() -> None:
    doc = golden_doc()
    doc["plots"][0]["trees"].append({"species": "Casuarina equisetifolia", "dbh_cm": 5.0})
    with pytest.raises(UnknownSpeciesError, match="Casuarina"):
        build(doc)


def test_reversal_gives_negative_onchain_units() -> None:
    doc = golden_doc()
    doc["period"] = {"kind": "verification", "start_year": 2031, "end_year": 2033}
    doc["include_soc"] = False
    doc["previous"] = {"tree_stock_tco2e": 9000.0, "cumulative_removals_tco2e": 9500.0}
    report = build(doc)
    assert report.lccc_units < 0
    assert report.lccc_units == -report.result.replacement_required_units
    assert any("Reversal" in w for w in report.warnings)


def test_partial_strata_coverage_warns() -> None:
    doc = golden_doc()
    doc["strata_area_ha"] = {"planted": 80.0}
    assert any("only stratified area" in w for w in build(doc).warnings)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["strata_area_ha"].update(other=5.0), "Strata without plots"),
        (lambda d: d["strata_area_ha"].update(planted=150.0), "exceed the project area"),
    ],
)
def test_build_rejects_inconsistent_areas(mutate: Any, message: str) -> None:
    doc = golden_doc()
    mutate(doc)
    with pytest.raises(ValueError, match=message):
        build(doc)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["plots"][0].update(stratum="nowhere"), "without an area"),
        (lambda d: d["plots"][1].update(code="P1"), "unique"),
        (lambda d: d["plots"][0].update(lat=20.7), "both lat and lon"),
        (lambda d: d["period"].update(end_year=2026), "after start_year"),
        (lambda d: d["period"].update(start_year=2025, end_year=2030), "before the project"),
        (lambda d: d["strata_area_ha"].update(planted=0.0), "positive"),
        (lambda d: d["project"].update(methodology="vm0033_v2_1"), "ccts_bm_fr05_001"),
        (lambda d: d.update(surprise=1), "Extra inputs"),
    ],
)
def test_input_validation(mutate: Any, message: str) -> None:
    doc = golden_doc()
    mutate(doc)
    with pytest.raises(ValidationError, match=message):
        ReportInputs.model_validate(doc)


def test_methodology_literal_matches_engine() -> None:
    from aqua_carbon.ccts import METHODOLOGY

    assert ReportInputs.model_validate(golden_doc()).project.methodology == METHODOLOGY
