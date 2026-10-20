"""Stewardship score (Phase 7 loyalty): pure, deterministic scoring functions.

An off-chain, non-transferable 0–100 score per project, aggregated per organisation. It is **not** a
token and is never stored: it is recomputed from the MRV/evidence/satellite tables on request. Every
number in the result is returned with the raw inputs and sub-scores it came from.

Formula (``STEWARDSHIP_V1`` / ``STEWARDSHIP_V2``; all constants live in :class:`StewardshipParams`)
-------------------------------------------------------------------------------------------

**1. Verification continuity** (weight 0.40). Inputs: every *decided* MRV report of the project
(approved / changes_requested / rejected) in decision order.

- ``streak`` = approved reports since the most recent rejection (a changes-requested decision does
  not break the streak; a rejection resets it to 0).
- ``streak_score = 100 × (1 − exp(−streak / 2))`` (diminishing returns: 1 → 39.3, 2 → 63.2,
  3 → 77.7, 5 → 91.8).
- ``penalty = 10 × changes_requested + 25 × rejected`` among the last 6 decisions.
- ``continuity = max(0, streak_score − penalty)``.
- Insufficient data: no decided report yet.

**2. Satellite-confirmed survival** (weight 0.35). Inputs: Sentinel-2 scene observations with
``clear_fraction ≥ 0.5`` and a finite NDVI p50 over the project polygon.

- Baseline window = the 365 days before the project start date; latest window = the 365 days up to
  ``as_of``. Each needs ≥ 3 clear scenes, and the latest window must start on/after the project start.
- ``Δ = median(latest NDVI p50) − median(baseline NDVI p50)`` (absolute NDVI units; absolute rather
  than relative change so a bare-mudflat baseline near 0 does not explode the ratio).
- ``ndvi_score = 100`` if ``Δ ≥ −0.02`` (maintained/increased, within noise); ``0`` if
  ``Δ ≤ −0.10`` (decline beyond threshold); linear in between.
- ``survival = max(0, ndvi_score − 20 × unresolved high-severity alerts)``. An alert is *unresolved*
  when it is high severity, observed in the latest window, and **not** acknowledged by an
  independent staff user (platform role verifier / registry_admin who is not a member of the
  project's organisation). Acknowledgements by the project's own organisation, or with no recorded
  acknowledger, do not clear the penalty (an org cannot acknowledge away its own loss alerts).
- MVI p50 medians of the same windows are reported as context only (not scored).
- Insufficient data: either window lacks clear scenes, or the project is < 12 months old.

**2b. Survival from classified mangrove area** (``STEWARDSHIP_V2`` only; replaces the NDVI rule
above when its inputs exist, otherwise ``stewardship-v2`` falls back to the NDVI rule unchanged).
Inputs: ecosystem-classifier results per dry season (Nov *s* – Mar *s+1*, ending 31 March *s+1*)
of one model version (the version of the latest season), mangrove area in ha.

- ``latest`` = the latest classified season that ended on/before ``as_of`` and started on/after the
  project start (a post-start look); ``baseline`` = the latest classified season that ended before
  the project start, or else the earliest classified season before ``latest``.
- Both must exist, be different seasons, come from the same model version, and the baseline area
  must be ≥ ``area_min_baseline_ha`` (1 ha; a relative change of a near-zero area is noise).
- ``change = 100 × (latest_ha − baseline_ha) / baseline_ha`` (percent of the baseline area).
- ``area_score = 100`` if ``change ≥ −5 %`` (maintained/increased, within classifier noise); ``0`` if
  ``change ≤ −20 %``; linear in between.
- ``survival = max(0, area_score − 20 × unresolved high-severity alerts)`` (same alert rule; the
  classifier's own ``mangrove_area_loss`` alerts count like vegetation-loss alerts).
- The NDVI inputs are still reported (context) and ``inputs.method`` says which rule scored.

Version history: ``stewardship-v1`` = components 1, 2 and 3 (NDVI survival only);
``stewardship-v2`` = the same with 2b (the default since the classifier was served). Every result
carries ``score_version``; both parameter sets stay importable, so a v1 score can be reproduced.

**3. Evidence quality** (weight 0.25). Inputs: the latest monitoring period (by end date) with at
least one automatically checked or rejected field submission.

- ``trust = median trust_score`` of ``checked`` submissions (0–100), weight 0.50.
- ``acceptance = 100 × (1 − rejected / (checked + rejected))``, weight 0.25.
- ``plot_coverage = 100 × plots with ≥ 1 checked photo in the period / project plots``, weight 0.25.
- Sub-scores that cannot be computed (no checked photo → no trust; no plots → no coverage) are
  dropped and the remaining weights renormalised (flagged in ``notes``).
- Insufficient data: no checked or rejected submission in any period.

**Project total** = Σ wᵢ·scoreᵢ / Σ wᵢ over the *available* components (renormalised and flagged
when one is missing; never scored as 0 or 100). No component available → ``score = None``.

**Organisation** = area-weighted (``area_ha``) mean of project totals over projects that have a total;
each organisation component is the area-weighted mean over projects where it is available.

**Forward-funding eligibility** (:func:`eligibility_for_forward_funding`, ``FORWARD_FUNDING_V1``):
organisation score ≥ 60, continuity and survival both available, organisation survival ≥ 50, at
least one project with an approved-report streak ≥ 1, and no unresolved high-severity alert (same
rule as the survival penalty).
"""

import math
import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any

from aqua_core.models.enums import ReportStatus

CONTINUITY = "verification_continuity"
SURVIVAL = "satellite_survival"
EVIDENCE = "evidence_quality"
DECIDED_STATUSES = (ReportStatus.APPROVED, ReportStatus.CHANGES_REQUESTED, ReportStatus.REJECTED)


@dataclass(frozen=True)
class StewardshipParams:
    """Every weight and threshold of one score version. Change values → bump ``version``."""

    version: str
    weights: dict[str, float]
    # continuity
    streak_tau: float
    changes_requested_penalty: float
    rejected_penalty: float
    penalty_window: int
    # survival
    clear_fraction_min: float
    min_clear_scenes: int
    window_days: int
    ndvi_tolerance: float  # Δ ≥ −tolerance → 100
    ndvi_decline_threshold: float  # Δ ≤ −threshold → 0
    # per unresolved high-severity alert observed in the latest window (cleared only by an
    # acknowledgement from independent staff: verifier/registry_admin outside the project's org)
    high_alert_penalty: float
    alert_rule: str
    # evidence
    evidence_weights: dict[str, float]
    # survival from classified mangrove area (stewardship-v2; ignored when False)
    use_classification: bool = False
    area_tolerance_pct: float = 5.0  # change ≥ −tolerance → 100
    area_decline_pct: float = 20.0  # change ≤ −decline → 0
    area_min_baseline_ha: float = 1.0


STEWARDSHIP_V1 = StewardshipParams(
    version="stewardship-v1",
    weights={CONTINUITY: 0.40, SURVIVAL: 0.35, EVIDENCE: 0.25},
    streak_tau=2.0,
    changes_requested_penalty=10.0,
    rejected_penalty=25.0,
    penalty_window=6,
    clear_fraction_min=0.5,
    min_clear_scenes=3,
    window_days=365,
    ndvi_tolerance=0.02,
    ndvi_decline_threshold=0.10,
    high_alert_penalty=20.0,
    alert_rule=(
        "high-severity alerts observed in the latest window count unless acknowledged by a "
        "verifier/registry_admin who is not a member of the project's organisation"
    ),
    evidence_weights={"trust": 0.50, "acceptance": 0.25, "plot_coverage": 0.25},
)


STEWARDSHIP_V2 = replace(STEWARDSHIP_V1, version="stewardship-v2", use_classification=True)


@dataclass(frozen=True)
class ForwardFundingParams:
    version: str
    min_score: float
    min_survival: float


FORWARD_FUNDING_V1 = ForwardFundingParams(
    version="forward-funding-v1", min_score=60.0, min_survival=50.0
)


# ---------------------------------------------------------------- inputs


@dataclass(frozen=True)
class ReportDecision:
    report_id: str
    period_id: str
    version: int
    status: ReportStatus
    decided_at: datetime


@dataclass(frozen=True)
class SceneValue:
    acquired_on: date
    clear_fraction: float
    ndvi_p50: float | None
    mvi_p50: float | None = None


@dataclass(frozen=True)
class SeasonClassification:
    """Mangrove area of one dry-season classification (the latest run of that season)."""

    classification_id: str
    season_start_year: int  # Nov <year> – Mar <year + 1>
    model_version: str
    mangrove_ha: float

    @property
    def season_end(self) -> date:
        return date(self.season_start_year + 1, 3, 31)

    @property
    def season_start(self) -> date:
        return date(self.season_start_year, 11, 1)


@dataclass(frozen=True)
class SurvivalInputs:
    project_start: date
    as_of: date
    scenes: Sequence[SceneValue]  # baseline + latest windows (others are ignored)
    unresolved_high_alerts: int  # see StewardshipParams.high_alert_penalty for the rule
    independently_cleared_high_alerts: int = 0  # context only
    classifications: Sequence[SeasonClassification] = ()  # stewardship-v2 only


@dataclass(frozen=True)
class EvidenceInputs:
    period_id: str | None  # None → no period with checked/rejected evidence
    checked_trust_scores: Sequence[int]
    rejected: int
    pending: int
    plots_total: int
    plots_photographed: int


# ---------------------------------------------------------------- outputs


@dataclass(frozen=True)
class ComponentScore:
    name: str
    weight: float
    score: float | None
    insufficient_data: bool
    sub_scores: dict[str, float | None]
    inputs: dict[str, Any]
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProjectStewardship:
    project_id: str
    area_ha: float
    score: float | None
    insufficient_data: bool
    renormalised: bool
    components: list[ComponentScore]
    notes: list[str]
    score_version: str
    computed_at: datetime


@dataclass(frozen=True)
class OrgStewardship:
    org_id: str
    score: float | None
    insufficient_data: bool
    components: dict[str, float | None]
    projects: list[ProjectStewardship]
    notes: list[str]
    score_version: str
    computed_at: datetime
    params: dict[str, Any]


def _r(value: float) -> float:
    return round(value, 2)


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


# ---------------------------------------------------------------- components


def score_continuity(
    decisions: Sequence[ReportDecision], params: StewardshipParams = STEWARDSHIP_V1
) -> ComponentScore:
    weight = params.weights[CONTINUITY]
    ordered = sorted(
        (d for d in decisions if d.status in DECIDED_STATUSES), key=lambda d: d.decided_at
    )
    listing = [
        {
            "report_id": d.report_id,
            "period_id": d.period_id,
            "version": d.version,
            "status": d.status.value,
            "decided_at": d.decided_at.isoformat(),
        }
        for d in ordered
    ]
    if not ordered:
        return ComponentScore(
            CONTINUITY,
            weight,
            None,
            True,
            {},
            {"decisions": listing},
            ["no verifier decision on any MRV report yet"],
        )
    streak = 0
    for d in reversed(ordered):
        if d.status is ReportStatus.REJECTED:
            break
        if d.status is ReportStatus.APPROVED:
            streak += 1
    window = ordered[-params.penalty_window :]
    changes = sum(d.status is ReportStatus.CHANGES_REQUESTED for d in window)
    rejected = sum(d.status is ReportStatus.REJECTED for d in window)
    streak_score = 100.0 * (1.0 - math.exp(-streak / params.streak_tau))
    penalty = changes * params.changes_requested_penalty + rejected * params.rejected_penalty
    return ComponentScore(
        CONTINUITY,
        weight,
        _r(_clamp(streak_score - penalty)),
        False,
        {"streak_score": _r(streak_score), "penalty": _r(penalty)},
        {
            "approved_streak": streak,
            "changes_requested_in_window": changes,
            "rejected_in_window": rejected,
            "penalty_window": params.penalty_window,
            "decisions": listing,
        },
    )


def _median(values: Sequence[float]) -> float | None:
    return round(statistics.median(values), 6) if values else None


def score_survival(
    inp: SurvivalInputs, params: StewardshipParams = STEWARDSHIP_V1
) -> ComponentScore:
    weight = params.weights[SURVIVAL]
    window = timedelta(days=params.window_days)
    baseline_from, baseline_to = inp.project_start - window, inp.project_start  # [from, to)
    latest_from, latest_to = inp.as_of - window, inp.as_of  # (from, to]

    def usable(s: SceneValue) -> bool:
        return (
            s.clear_fraction >= params.clear_fraction_min
            and s.ndvi_p50 is not None
            and math.isfinite(s.ndvi_p50)
        )

    baseline = [s for s in inp.scenes if baseline_from <= s.acquired_on < baseline_to]
    latest = [s for s in inp.scenes if latest_from < s.acquired_on <= latest_to]
    base_ok = [s for s in baseline if usable(s)]
    late_ok = [s for s in latest if usable(s)]

    def mvi(scenes: list[SceneValue]) -> float | None:
        vals = [s.mvi_p50 for s in scenes if s.mvi_p50 is not None and math.isfinite(s.mvi_p50)]
        return _median(vals)

    base_ndvi = _median([s.ndvi_p50 for s in base_ok if s.ndvi_p50 is not None])
    late_ndvi = _median([s.ndvi_p50 for s in late_ok if s.ndvi_p50 is not None])
    inputs: dict[str, Any] = {
        "metric": "ndvi_p50",
        "clear_fraction_min": params.clear_fraction_min,
        "min_clear_scenes": params.min_clear_scenes,
        "baseline_window": [baseline_from.isoformat(), baseline_to.isoformat()],
        "latest_window": [latest_from.isoformat(), latest_to.isoformat()],
        "baseline_scenes": len(baseline),
        "baseline_clear_scenes": len(base_ok),
        "latest_scenes": len(latest),
        "latest_clear_scenes": len(late_ok),
        "baseline_ndvi_p50_median": base_ndvi,
        "latest_ndvi_p50_median": late_ndvi,
        "baseline_mvi_p50_median": mvi(base_ok),
        "latest_mvi_p50_median": mvi(late_ok),
        "alert_window": [latest_from.isoformat(), latest_to.isoformat()],
        "unresolved_high_severity_alerts": inp.unresolved_high_alerts,
        "independently_cleared_high_severity_alerts": inp.independently_cleared_high_alerts,
    }
    missing: list[str] = []
    if latest_from < inp.project_start:
        missing.append("project is younger than 12 months: no post-start comparison window yet")
    if len(base_ok) < params.min_clear_scenes:
        missing.append(f"baseline year has fewer than {params.min_clear_scenes} clear scenes")
    if len(late_ok) < params.min_clear_scenes:
        missing.append(f"latest 12 months have fewer than {params.min_clear_scenes} clear scenes")
    notes = list(missing)
    if inp.unresolved_high_alerts:
        notes.append(
            f"{inp.unresolved_high_alerts} unresolved high-severity vegetation-loss alert(s)"
        )
    if params.use_classification:
        pair = classification_pair(inp)
        if pair is not None and pair[0].mangrove_ha >= params.area_min_baseline_ha:
            return _area_survival(pair, inp, params, inputs, notes)
        inputs["method"] = "ndvi_p50"
        notes.append(
            "no two comparable dry-season classifications (pre/post start, same model, baseline "
            f">= {params.area_min_baseline_ha:g} ha mangrove): NDVI rule used"
        )
    if missing or base_ndvi is None or late_ndvi is None:
        return ComponentScore(SURVIVAL, weight, None, True, {}, inputs, notes)

    delta = late_ndvi - base_ndvi
    inputs["ndvi_p50_delta"] = round(delta, 6)
    tol, drop = params.ndvi_tolerance, params.ndvi_decline_threshold
    if delta >= -tol:
        ndvi_score = 100.0
    elif delta <= -drop:
        ndvi_score = 0.0
    else:
        ndvi_score = 100.0 * (delta + drop) / (drop - tol)
    alert_penalty = inp.unresolved_high_alerts * params.high_alert_penalty
    return ComponentScore(
        SURVIVAL,
        weight,
        _r(_clamp(ndvi_score - alert_penalty)),
        False,
        {"ndvi_change_score": _r(ndvi_score), "alert_penalty": _r(alert_penalty)},
        inputs,
        notes,
    )


def classification_pair(
    inp: SurvivalInputs,
) -> tuple[SeasonClassification, SeasonClassification] | None:
    """(baseline, latest) classifications for the area rule, or None (see section 2b)."""
    ended = [c for c in inp.classifications if c.season_end <= inp.as_of]
    post_start = [c for c in ended if c.season_start >= inp.project_start]
    if not post_start:
        return None
    latest = max(post_start, key=lambda c: c.season_start_year)
    same = [
        c
        for c in ended
        if c.model_version == latest.model_version
        and c.season_start_year < latest.season_start_year
    ]
    if not same:
        return None
    before = [c for c in same if c.season_end < inp.project_start]
    baseline = (
        max(before, key=lambda c: c.season_start_year)
        if before
        else min(same, key=lambda c: c.season_start_year)
    )
    return baseline, latest


def _area_survival(
    pair: tuple[SeasonClassification, SeasonClassification],
    inp: SurvivalInputs,
    params: StewardshipParams,
    inputs: dict[str, Any],
    notes: list[str],
) -> ComponentScore:
    baseline, latest = pair
    change = 100.0 * (latest.mangrove_ha - baseline.mangrove_ha) / baseline.mangrove_ha
    tol, drop = params.area_tolerance_pct, params.area_decline_pct
    if change >= -tol:
        area_score = 100.0
    elif change <= -drop:
        area_score = 0.0
    else:
        area_score = 100.0 * (change + drop) / (drop - tol)
    inputs.update(
        {
            "method": "classification_area",
            "model_version": latest.model_version,
            "baseline_season_start_year": baseline.season_start_year,
            "baseline_mangrove_ha": baseline.mangrove_ha,
            "baseline_classification_id": baseline.classification_id,
            "latest_season_start_year": latest.season_start_year,
            "latest_mangrove_ha": latest.mangrove_ha,
            "latest_classification_id": latest.classification_id,
            "mangrove_area_change_pct": round(change, 6),
            "area_tolerance_pct": tol,
            "area_decline_pct": drop,
        }
    )
    # NDVI-window shortfalls do not apply to the area rule.
    kept = [n for n in notes if "clear scenes" not in n and "younger than 12 months" not in n]
    alert_penalty = inp.unresolved_high_alerts * params.high_alert_penalty
    return ComponentScore(
        SURVIVAL,
        params.weights[SURVIVAL],
        _r(_clamp(area_score - alert_penalty)),
        False,
        {"area_change_score": _r(area_score), "alert_penalty": _r(alert_penalty)},
        inputs,
        kept,
    )


def score_evidence(
    inp: EvidenceInputs, params: StewardshipParams = STEWARDSHIP_V1
) -> ComponentScore:
    weight = params.weights[EVIDENCE]
    checked = len(inp.checked_trust_scores)
    decided = checked + inp.rejected
    median_trust = _median([float(t) for t in inp.checked_trust_scores])
    inputs: dict[str, Any] = {
        "period_id": inp.period_id,
        "checked_submissions": checked,
        "rejected_submissions": inp.rejected,
        "pending_submissions": inp.pending,
        "median_trust_score": median_trust,
        "rejected_share": round(inp.rejected / decided, 6) if decided else None,
        "plots_total": inp.plots_total,
        "plots_photographed": inp.plots_photographed,
    }
    if inp.period_id is None or decided == 0:
        return ComponentScore(
            EVIDENCE,
            weight,
            None,
            True,
            {},
            inputs,
            ["no automatically checked field evidence in any monitoring period yet"],
        )
    subs: dict[str, float | None] = {
        "trust": _clamp(median_trust) if median_trust is not None else None,
        "acceptance": 100.0 * (1.0 - inp.rejected / decided),
        "plot_coverage": (
            _clamp(100.0 * inp.plots_photographed / inp.plots_total) if inp.plots_total else None
        ),
    }
    notes = []
    if subs["trust"] is None:
        notes.append("no checked submission in the period: trust sub-score unavailable")
    if subs["plot_coverage"] is None:
        notes.append("project has no sample plots: plot-coverage sub-score unavailable")
    if notes:
        notes.append("evidence sub-weights renormalised over the available sub-scores")
    score, _ = weighted_mean(
        {k: v for k, v in subs.items() if v is not None}, params.evidence_weights
    )
    return ComponentScore(
        EVIDENCE,
        weight,
        _r(score) if score is not None else None,
        False,
        {k: (_r(v) if v is not None else None) for k, v in subs.items()},
        inputs,
        notes,
    )


def weighted_mean(values: dict[str, float], weights: dict[str, float]) -> tuple[float | None, bool]:
    """Σ wᵢ·vᵢ / Σ wᵢ over the keys present in ``values``; returns (mean, renormalised)."""
    total_w = sum(weights[k] for k in values)
    if total_w <= 0:
        return None, False
    mean = sum(weights[k] * v for k, v in values.items()) / total_w
    return mean, set(values) != set(weights)


# ---------------------------------------------------------------- project / organisation


def score_project(
    *,
    project_id: str,
    area_ha: float,
    decisions: Sequence[ReportDecision],
    survival: SurvivalInputs,
    evidence: EvidenceInputs,
    computed_at: datetime,
    params: StewardshipParams = STEWARDSHIP_V1,
) -> ProjectStewardship:
    components = [
        score_continuity(decisions, params),
        score_survival(survival, params),
        score_evidence(evidence, params),
    ]
    available = {c.name: c.score for c in components if c.score is not None}
    total, renormalised = weighted_mean(available, params.weights)
    notes: list[str] = []
    missing = [c.name for c in components if c.score is None]
    if total is None:
        notes.append("insufficient data for every component: no score")
    elif renormalised:
        notes.append(
            "weights renormalised over available components; missing: " + ", ".join(missing)
        )
    return ProjectStewardship(
        project_id=project_id,
        area_ha=area_ha,
        score=_r(total) if total is not None else None,
        insufficient_data=total is None,
        renormalised=renormalised,
        components=components,
        notes=notes,
        score_version=params.version,
        computed_at=computed_at,
    )


def _area_weighted(pairs: list[tuple[float, float]]) -> float | None:
    area = sum(a for a, _ in pairs)
    if not pairs or area <= 0:
        return None
    return _r(sum(a * v for a, v in pairs) / area)


def aggregate_org(
    *,
    org_id: str,
    projects: Sequence[ProjectStewardship],
    computed_at: datetime,
    params: StewardshipParams = STEWARDSHIP_V1,
) -> OrgStewardship:
    scored = [(p.area_ha, p.score) for p in projects if p.score is not None]
    total = _area_weighted([(a, s) for a, s in scored if s is not None])
    components: dict[str, float | None] = {}
    for name in params.weights:
        pairs = [
            (p.area_ha, c.score)
            for p in projects
            for c in p.components
            if c.name == name and c.score is not None
        ]
        components[name] = _area_weighted([(a, s) for a, s in pairs if s is not None])
    notes: list[str] = []
    if not projects:
        notes.append("organisation has no submitted projects")
    elif total is None:
        notes.append("insufficient data on every project: no score")
    elif len(scored) < len(projects):
        notes.append(f"{len(projects) - len(scored)} project(s) without data are not weighted")
    return OrgStewardship(
        org_id=org_id,
        score=total,
        insufficient_data=total is None,
        components=components,
        projects=list(projects),
        notes=notes,
        score_version=params.version,
        computed_at=computed_at,
        params=asdict(params),
    )


def eligibility_for_forward_funding(
    org: OrgStewardship, params: ForwardFundingParams = FORWARD_FUNDING_V1
) -> tuple[bool, list[str]]:
    """Whether the organisation may offer into forward-funding (reverse-auction) requests.

    Returns ``(eligible, reasons)``; ``reasons`` lists every check as ``"pass: …"`` or
    ``"fail: …"`` so the relayer/UI can explain the outcome. Thresholds: :data:`FORWARD_FUNDING_V1`.
    """
    checks: list[tuple[bool, str]] = []
    score = org.score
    checks.append(
        (
            score is not None and score >= params.min_score,
            f"stewardship score {score if score is not None else 'n/a'} "
            f"(minimum {params.min_score:g})",
        )
    )
    continuity = org.components.get(CONTINUITY)
    survival = org.components.get(SURVIVAL)
    checks.append((continuity is not None, "verification history available"))
    checks.append(
        (
            survival is not None and survival >= params.min_survival,
            f"satellite survival {survival if survival is not None else 'n/a'} "
            f"(minimum {params.min_survival:g})",
        )
    )
    streaks = [
        int(c.inputs.get("approved_streak", 0))
        for p in org.projects
        for c in p.components
        if c.name == CONTINUITY
    ]
    checks.append((max(streaks, default=0) >= 1, "at least one project with an approved report"))
    alerts = sum(
        int(c.inputs.get("unresolved_high_severity_alerts", 0))
        for p in org.projects
        for c in p.components
        if c.name == SURVIVAL
    )
    checks.append((alerts == 0, f"{alerts} unresolved high-severity alert(s) (maximum 0)"))
    reasons = [f"{'pass' if ok else 'fail'}: {msg}" for ok, msg in checks]
    return all(ok for ok, _ in checks), reasons
