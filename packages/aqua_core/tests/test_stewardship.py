"""Pure tests of the stewardship scoring (no database). Inputs are synthetic test values."""

import itertools
import math
import random
from datetime import UTC, date, datetime, timedelta

import pytest

from aqua_core.models.enums import ReportStatus
from aqua_core.stewardship import (
    CONTINUITY,
    EVIDENCE,
    STEWARDSHIP_V1,
    STEWARDSHIP_V2,
    SURVIVAL,
    EvidenceInputs,
    ReportDecision,
    SceneValue,
    SeasonClassification,
    SurvivalInputs,
    aggregate_org,
    eligibility_for_forward_funding,
    score_continuity,
    score_evidence,
    score_project,
    score_survival,
)

NOW = datetime(2030, 6, 1, tzinfo=UTC)
START = date(2027, 1, 1)
A, C, R = ReportStatus.APPROVED, ReportStatus.CHANGES_REQUESTED, ReportStatus.REJECTED


def decisions(*statuses: ReportStatus) -> list[ReportDecision]:
    t0 = datetime(2028, 1, 1, tzinfo=UTC)
    return [
        ReportDecision(f"r{i}", f"p{i}", 1, s, t0 + timedelta(days=30 * i))
        for i, s in enumerate(statuses)
    ]


def scenes(baseline: float, latest: float, n: int = 4, clear: float = 0.9) -> list[SceneValue]:
    out = [SceneValue(START - timedelta(days=30 * (i + 1)), clear, baseline, 1.5) for i in range(n)]
    out += [
        SceneValue(NOW.date() - timedelta(days=30 * (i + 1)), clear, latest, 1.6) for i in range(n)
    ]
    return out


def survival(baseline: float = 0.6, latest: float = 0.62, alerts: int = 0) -> SurvivalInputs:
    return SurvivalInputs(START, NOW.date(), scenes(baseline, latest), alerts)


def evidence(trust: list[int], rejected: int = 0, plots: int = 4, shot: int = 4) -> EvidenceInputs:
    return EvidenceInputs("period-1", trust, rejected, 0, plots, shot)


# ---------------------------------------------------------------- continuity


def test_continuity_diminishing_returns_and_monotonic_in_streak() -> None:
    values = [score_continuity(decisions(*[A] * n)).score for n in range(1, 9)]
    assert values[0] == pytest.approx(100 * (1 - math.exp(-0.5)), abs=0.01)
    gains = [b - a for a, b in itertools.pairwise(values)]
    assert all(g > 0 for g in gains)
    assert all(g2 < g1 for g1, g2 in itertools.pairwise(gains))
    assert all(0 <= v <= 100 for v in values)


def test_rejection_resets_streak_and_changes_requested_penalises() -> None:
    after_reject = score_continuity(decisions(A, A, A, R, A))
    assert after_reject.inputs["approved_streak"] == 1
    assert after_reject.sub_scores["penalty"] == 25
    with_changes = score_continuity(decisions(A, C, A))
    assert with_changes.inputs["approved_streak"] == 2
    assert with_changes.score == pytest.approx(63.21 - 10, abs=0.01)
    assert score_continuity(decisions(R)).score == 0


def test_continuity_appending_an_approval_never_lowers_the_score() -> None:
    rng = random.Random(7)  # noqa: S311 (deterministic test data)
    for _ in range(300):
        history = [rng.choice([A, A, C, R]) for _ in range(rng.randint(1, 10))]
        before = score_continuity(decisions(*history)).score
        after = score_continuity(decisions(*history, A)).score
        assert before is not None
        assert after is not None
        assert 0 <= before <= 100
        assert after >= before


def test_continuity_without_decisions_is_insufficient_not_zero() -> None:
    c = score_continuity([])
    assert c.score is None
    assert c.insufficient_data


# ---------------------------------------------------------------- survival


@pytest.mark.parametrize(
    ("baseline", "latest", "expected"),
    [(0.5, 0.7, 100.0), (0.6, 0.59, 100.0), (0.6, 0.54, 50.0), (0.6, 0.4, 0.0)],
)
def test_survival_ndvi_change_mapping(baseline: float, latest: float, expected: float) -> None:
    c = score_survival(survival(baseline, latest))
    assert not c.insufficient_data
    assert c.score == pytest.approx(expected, abs=0.01)


def test_survival_unresolved_high_alerts_reduce_score() -> None:
    assert score_survival(survival(alerts=2)).score == 60.0
    assert score_survival(survival(alerts=9)).score == 0.0


def test_survival_ignores_cloudy_scenes_and_flags_insufficient() -> None:
    cloudy = SurvivalInputs(START, NOW.date(), scenes(0.6, 0.6, clear=0.3), 0)
    c = score_survival(cloudy)
    assert c.score is None
    assert c.insufficient_data
    assert c.inputs["baseline_scenes"] == 4
    assert c.inputs["baseline_clear_scenes"] == 0


def test_survival_young_project_is_insufficient() -> None:
    young = SurvivalInputs(NOW.date() - timedelta(days=100), NOW.date(), [], 0)
    c = score_survival(young)
    assert c.insufficient_data
    assert any("younger than 12 months" in n for n in c.notes)


# ---------------------------------------------------------------- evidence


def test_evidence_monotonic_in_trust_and_bounded() -> None:
    values = [score_evidence(evidence([t, t, t])).score for t in range(0, 101, 5)]
    assert values == sorted(values)
    assert values[0] == 50.0  # trust 0, no rejections, full coverage
    assert values[-1] == 100.0


def test_evidence_renormalises_without_plots_and_without_checked() -> None:
    no_plots = score_evidence(evidence([80], plots=0, shot=0))
    assert no_plots.sub_scores["plot_coverage"] is None
    assert no_plots.score == pytest.approx((0.5 * 80 + 0.25 * 100) / 0.75, abs=0.01)
    assert any("renormalised" in n for n in no_plots.notes)
    all_rejected = score_evidence(evidence([], rejected=3, plots=2, shot=0))
    assert all_rejected.sub_scores["trust"] is None
    assert all_rejected.score == 0.0
    nothing = score_evidence(EvidenceInputs(None, [], 0, 2, 3, 0))
    assert nothing.insufficient_data
    assert nothing.score is None


# ---------------------------------------------------------------- totals


def test_project_total_renormalises_over_available_components() -> None:
    full = score_project(
        project_id="p",
        area_ha=10,
        decisions=decisions(A, A),
        survival=survival(),
        evidence=evidence([90]),
        computed_at=NOW,
    )
    comps = {c.name: c.score for c in full.components}
    expected = sum(STEWARDSHIP_V1.weights[k] * v for k, v in comps.items())
    assert full.score == pytest.approx(expected, abs=0.01)
    assert not full.renormalised

    partial = score_project(
        project_id="p",
        area_ha=10,
        decisions=decisions(A, A),
        survival=SurvivalInputs(START, NOW.date(), [], 0),
        evidence=evidence([90]),
        computed_at=NOW,
    )
    c, e = (
        next(x.score for x in partial.components if x.name == n) for n in (CONTINUITY, EVIDENCE)
    )
    assert partial.renormalised
    assert partial.score == pytest.approx((0.40 * c + 0.25 * e) / 0.65, abs=0.01)
    assert any(SURVIVAL in n for n in partial.notes)

    empty = score_project(
        project_id="p",
        area_ha=10,
        decisions=[],
        survival=SurvivalInputs(START, NOW.date(), [], 0),
        evidence=EvidenceInputs(None, [], 0, 0, 0, 0),
        computed_at=NOW,
    )
    assert empty.score is None
    assert empty.insufficient_data
    assert empty.score_version == "stewardship-v1"


def test_random_inputs_are_bounded() -> None:
    rng = random.Random(11)  # noqa: S311 (deterministic test data)
    for _ in range(200):
        p = score_project(
            project_id="p",
            area_ha=1,
            decisions=decisions(*[rng.choice([A, C, R]) for _ in range(rng.randint(0, 8))]),
            survival=survival(rng.uniform(-1, 1), rng.uniform(-1, 1), rng.randint(0, 3)),
            evidence=evidence(
                [rng.randint(0, 100) for _ in range(rng.randint(0, 5))],
                rng.randint(0, 3),
                plots=4,
                shot=rng.randint(0, 4),
            ),
            computed_at=NOW,
        )
        assert p.score is None or 0 <= p.score <= 100
        for c in p.components:
            assert c.score is None or 0 <= c.score <= 100


def test_org_area_weighting_and_forward_funding() -> None:
    good = score_project(
        project_id="a",
        area_ha=100,
        decisions=decisions(A, A, A),
        survival=survival(),
        evidence=evidence([90]),
        computed_at=NOW,
    )
    weak = score_project(
        project_id="b",
        area_ha=300,
        decisions=decisions(R),
        survival=survival(0.6, 0.4),
        evidence=evidence([20], rejected=2),
        computed_at=NOW,
    )
    nodata = score_project(
        project_id="c",
        area_ha=1000,
        decisions=[],
        survival=SurvivalInputs(START, NOW.date(), [], 0),
        evidence=EvidenceInputs(None, [], 0, 0, 0, 0),
        computed_at=NOW,
    )
    org = aggregate_org(org_id="o", projects=[good, weak, nodata], computed_at=NOW)
    assert good.score is not None
    assert weak.score is not None
    assert org.score == pytest.approx((100 * good.score + 300 * weak.score) / 400, abs=0.01)
    assert any("without data" in n for n in org.notes)
    assert org.params["weights"] == STEWARDSHIP_V1.weights

    ok, reasons = eligibility_for_forward_funding(
        aggregate_org(org_id="o", projects=[good], computed_at=NOW)
    )
    assert ok
    assert all(r.startswith("pass") for r in reasons)
    ok, reasons = eligibility_for_forward_funding(org)
    assert not ok  # weak project drags survival / score down
    alerted = score_project(
        project_id="a",
        area_ha=300,
        decisions=decisions(A, A, A),
        survival=survival(alerts=1),
        evidence=evidence([90]),
        computed_at=NOW,
    )
    ok, reasons = eligibility_for_forward_funding(
        aggregate_org(org_id="o", projects=[alerted], computed_at=NOW)
    )
    assert not ok
    assert "fail: 1 unresolved high-severity alert(s) (maximum 0)" in reasons
    empty = aggregate_org(org_id="o", projects=[], computed_at=NOW)
    assert empty.insufficient_data
    assert eligibility_for_forward_funding(empty)[0] is False


# ---------------------------------------------------------------- stewardship-v2 (classified area)


def classes(*pairs: tuple[int, float], version: str = "v1") -> list[SeasonClassification]:
    return [SeasonClassification(f"c{y}", y, version, ha) for y, ha in pairs]


def area_survival(cls: list[SeasonClassification], **kw: object) -> SurvivalInputs:
    base = survival(**kw)  # type: ignore[arg-type]
    return SurvivalInputs(START, NOW.date(), base.scenes, base.unresolved_high_alerts, 0, cls)


def test_v2_scores_survival_from_mangrove_area_change() -> None:
    # START 2027-01-01: season 2025-26 (ends Mar 2026) is the baseline, 2029-30 the latest.
    cls = classes((2024, 50.0), (2025, 100.0), (2027, 70.0), (2029, 88.0), (2030, 1.0))
    c = score_survival(area_survival(cls), STEWARDSHIP_V2)
    assert c.inputs["method"] == "classification_area"
    assert c.inputs["baseline_season_start_year"] == 2025
    assert c.inputs["latest_season_start_year"] == 2029  # 2030-31 has not ended by NOW
    assert c.inputs["mangrove_area_change_pct"] == pytest.approx(-12.0)
    assert c.sub_scores["area_change_score"] == pytest.approx(100 * (20 - 12) / 15, abs=0.01)
    assert c.score == c.sub_scores["area_change_score"]
    assert c.inputs["latest_ndvi_p50_median"] == pytest.approx(0.62)  # NDVI kept as context
    keep = score_survival(area_survival(classes((2025, 100.0), (2029, 96.0))), STEWARDSHIP_V2)
    assert keep.score == 100.0
    lost = score_survival(area_survival(classes((2025, 100.0), (2029, 79.0))), STEWARDSHIP_V2)
    assert lost.score == 0.0
    alert = score_survival(
        area_survival(classes((2025, 100.0), (2029, 96.0)), alerts=1), STEWARDSHIP_V2
    )
    assert alert.score == 80.0
    assert alert.sub_scores["alert_penalty"] == 20.0


def test_v2_baseline_falls_back_to_the_earliest_post_start_season() -> None:
    c = score_survival(
        area_survival(classes((2027, 40.0), (2028, 41.0), (2029, 42.0))), STEWARDSHIP_V2
    )
    assert c.inputs["baseline_season_start_year"] == 2027
    assert c.score == 100.0


@pytest.mark.parametrize(
    "cls",
    [
        [],  # nothing classified
        classes((2025, 100.0)),  # only a pre-start season
        classes((2029, 100.0)),  # only one post-start season
        classes((2025, 100.0), version="v0") + classes((2029, 90.0)),  # different models
        classes((2025, 0.5), (2029, 0.4)),  # baseline below 1 ha
    ],
)
def test_v2_falls_back_to_the_ndvi_rule(cls: list[SeasonClassification]) -> None:
    c = score_survival(area_survival(cls), STEWARDSHIP_V2)
    v1 = score_survival(area_survival(cls), STEWARDSHIP_V1)
    assert c.inputs["method"] == "ndvi_p50"
    assert c.score == v1.score == 100.0
    assert any(n.startswith("no two comparable") for n in c.notes)


def test_v1_ignores_classifications() -> None:
    cls = classes((2025, 100.0), (2029, 10.0))
    v1 = score_survival(area_survival(cls), STEWARDSHIP_V1)
    assert v1.score == 100.0
    assert "method" not in v1.inputs
    assert STEWARDSHIP_V2.version == "stewardship-v2"
    assert STEWARDSHIP_V2.weights == STEWARDSHIP_V1.weights
    assert not STEWARDSHIP_V1.use_classification
    assert STEWARDSHIP_V2.use_classification
