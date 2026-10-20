"""Loss alerts against a project's OWN history (pure functions): the Sentinel-2 NDVI rule below,
and the Sentinel-1 `sar_backscatter_drop` heuristic at the end of this module.

Rule (documented so org admins and verifiers can see exactly why an alert fired):

1. Only *clear* observations count: clear_fraction >= `min_clear_fraction` (default 0.6) of the
   polygon, with an NDVI value. Cloudy scenes neither raise nor clear an alert.
2. For each clear observation, the envelope is built from clear observations of the SAME calendar
   month in EARLIER years (mangrove phenology and tides differ by season, so January is compared
   with past Januaries). At least `min_history` values are required, otherwise the observation is
   "no baseline yet" and breaks any running sequence.
3. An observation is *below* the envelope when its value < historical P10 − `margin`.
4. An alert fires when `consecutive` (default 2) clear observations in a row are below. A single
   low scene (haze, high tide, residual cloud) never alerts on its own. One alert per sequence: it
   is dated on the observation that completed the sequence, so re-running detection on the same
   data produces the same (deduplicated) alert.
"""

import calendar
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from aqua_core.models import AlertKind, AlertSeverity

METRIC_NDVI_P50 = "ndvi_p50"


@dataclass(frozen=True)
class AlertRules:
    min_clear_fraction: float = 0.6
    margin: float = 0.05
    consecutive: int = 2
    min_history: int = 3
    # relative drop below the historical median at which severity escalates
    medium_drop: float = 0.2
    high_drop: float = 0.4


DEFAULT_RULES = AlertRules()


@dataclass(frozen=True)
class Observation:
    acquired_on: date
    clear_fraction: float
    value: float | None  # e.g. NDVI P50 over the clear pixels of the polygon
    item_id: str = ""


@dataclass(frozen=True)
class Envelope:
    month: int
    p10: float
    p50: float
    p90: float
    n: int
    first_year: int
    last_year: int
    threshold: float


@dataclass(frozen=True)
class AlertCandidate:
    kind: AlertKind
    metric: str
    observed_on: date
    observed_value: float
    severity: AlertSeverity
    expected: dict[str, Any]
    message: str
    item_ids: list[str] = field(default_factory=list)


def is_clear(obs: Observation, rules: AlertRules) -> bool:
    return (
        obs.value is not None
        and np.isfinite(obs.value)
        and obs.clear_fraction >= rules.min_clear_fraction
    )


def envelope(history: Iterable[Observation], at: Observation, rules: AlertRules) -> Envelope | None:
    """Same-calendar-month envelope from clear observations in years before `at`'s year."""
    past = [
        o
        for o in history
        if o.acquired_on.month == at.acquired_on.month
        and o.acquired_on.year < at.acquired_on.year
        and is_clear(o, rules)
    ]
    if len(past) < rules.min_history:
        return None
    values = np.array([o.value for o in past], dtype="float64")
    p10, p50, p90 = (float(v) for v in np.percentile(values, [10, 50, 90]))
    years = [o.acquired_on.year for o in past]
    return Envelope(
        month=at.acquired_on.month,
        p10=round(p10, 4),
        p50=round(p50, 4),
        p90=round(p90, 4),
        n=len(past),
        first_year=min(years),
        last_year=max(years),
        threshold=round(p10 - rules.margin, 4),
    )


def _severity(mean_value: float, env: Envelope, rules: AlertRules) -> AlertSeverity:
    if env.p50 <= 0:
        return AlertSeverity.MEDIUM
    drop = (env.p50 - mean_value) / env.p50
    if drop >= rules.high_drop:
        return AlertSeverity.HIGH
    if drop >= rules.medium_drop:
        return AlertSeverity.MEDIUM
    return AlertSeverity.LOW


def _message(run: Sequence[tuple[Observation, Envelope]], rules: AlertRules) -> str:
    last_env = run[-1][1]
    month = calendar.month_name[last_env.month]
    seq = ", ".join(f"{o.acquired_on.isoformat()} {o.value:.2f}" for o, _ in run)
    return (
        f"Vegetation loss suspected: NDVI (polygon median over clear pixels) fell below this "
        f"project's own historical envelope on {len(run)} consecutive clear observations "
        f"(clear fraction >= {rules.min_clear_fraction:.0%}): {seq}. "
        f"Latest threshold {last_env.threshold:.2f} = historical {month} P10 {last_env.p10:.2f} "
        f"− margin {rules.margin:.2f}; historical {month} median {last_env.p50:.2f} from "
        f"{last_env.n} clear observations ({last_env.first_year}–{last_env.last_year}). "
        "Possible causes: cutting, dieback, erosion or prolonged inundation. "
        "Check recent imagery and verify on the ground."
    )


def detect_vegetation_loss(
    observations: Iterable[Observation],
    rules: AlertRules = DEFAULT_RULES,
    *,
    since: date | None = None,
    existing: Collection[date] = (),
) -> list[AlertCandidate]:
    """Alerts for runs of `rules.consecutive` clear observations below the envelope.

    `since`: only return alerts completed on/after this date (the nightly job passes the first
    newly ingested acquisition date, so history is not re-alerted every night).
    `existing`: observed_on dates of alerts already stored (skipped: no duplicates).
    """
    if rules.consecutive < 1:
        raise ValueError("consecutive must be >= 1")
    clear = sorted((o for o in observations if is_clear(o, rules)), key=lambda o: o.acquired_on)
    by_month: dict[int, list[Observation]] = {}
    for o in clear:
        by_month.setdefault(o.acquired_on.month, []).append(o)
    out: list[AlertCandidate] = []
    run: list[tuple[Observation, Envelope]] = []
    for obs in clear:
        value = float(obs.value) if obs.value is not None else float("nan")
        env = envelope(by_month[obs.acquired_on.month], obs, rules)
        if env is None or not value < env.threshold:
            run = []
            continue
        run.append((obs, env))
        if len(run) != rules.consecutive:
            continue  # shorter: not yet confirmed; longer: same event, already alerted
        if (since is not None and obs.acquired_on < since) or obs.acquired_on in existing:
            continue
        mean_value = float(np.mean([o.value for o, _ in run]))
        out.append(
            AlertCandidate(
                kind=AlertKind.VEGETATION_LOSS,
                metric=METRIC_NDVI_P50,
                observed_on=obs.acquired_on,
                observed_value=round(value, 4),
                severity=_severity(mean_value, env, rules),
                expected={
                    "month": env.month,
                    "p10": env.p10,
                    "p50": env.p50,
                    "p90": env.p90,
                    "n": env.n,
                    "years": [env.first_year, env.last_year],
                    "margin": rules.margin,
                    "threshold": env.threshold,
                    "consecutive": rules.consecutive,
                    "min_clear_fraction": rules.min_clear_fraction,
                    "observations": [
                        {
                            "date": o.acquired_on.isoformat(),
                            "value": o.value,
                            "clear_fraction": o.clear_fraction,
                            "item_id": o.item_id,
                            "threshold": e.threshold,
                        }
                        for o, e in run
                    ],
                },
                message=_message(run, rules),
                item_ids=[o.item_id for o, _ in run],
            )
        )
    return out


# ---------------------------------------------------------------- Sentinel-1 heuristic
#
# `sar_backscatter_drop` (GEO_SAR_ALERTS_ENABLED, default off). A HEURISTIC, not a loss detector:
#
# 1. Only in-season acquisitions are evaluated: those acquired in the monsoon months
#    (`season_months`, GEO_S1_MONSOON_MONTHS, assumed to lie within one calendar year), with a VH dB
#    P50 and valid_fraction >= `min_valid_fraction`. Other acquisitions are ignored (neither raise
#    nor break a sequence).
# 2. Baseline: the median VH dB P50 of in-season acquisitions of EARLIER years on the SAME relative
#    orbit (backscatter depends on viewing geometry), at least `min_history` of them; otherwise the
#    acquisition has "no baseline yet" and breaks any running sequence.
# 3. Below: VH dB P50 < baseline median − `drop_db` (default 3 dB, i.e. half the backscattered power;
#    canopy removal lowers cross-pol volume scattering).
# 4. An alert fires when `consecutive` (default 3) evaluated acquisitions in a row are below, dated
#    on the one completing the sequence; one alert per sequence (deduplicated like the NDVI rule).
# 5. Severity is always MEDIUM: flooding, soil moisture, tides and speckle also move VH, so SAR alone
#    never raises a high alert. It must be confirmed with Sentinel-2 imagery or a field visit.

METRIC_VH_DB_P50 = "vh_db_p50"


@dataclass(frozen=True)
class SarAlertRules:
    drop_db: float = 3.0
    consecutive: int = 3
    min_history: int = 6
    min_valid_fraction: float = 0.6
    season_months: tuple[int, ...] = (6, 7, 8, 9)

    @classmethod
    def from_settings(cls, settings: Any) -> "SarAlertRules | None":
        """None unless both Sentinel-1 ingestion and SAR alerts are enabled."""
        if not (settings.geo_s1_enabled and settings.geo_sar_alerts_enabled):
            return None
        return cls(
            drop_db=settings.geo_sar_alert_drop_db,
            consecutive=settings.geo_sar_alert_consecutive,
            min_history=settings.geo_sar_alert_min_history,
            min_valid_fraction=settings.geo_sar_alert_min_valid_fraction,
            season_months=tuple(settings.geo_s1_monsoon_months),
        )


@dataclass(frozen=True)
class SarPoint:
    acquired_on: date
    valid_fraction: float
    value: float | None  # VH dB P50 over the valid pixels of the polygon
    key: str = ""  # acquisition key
    relative_orbit: int | None = None


@dataclass(frozen=True)
class SarBaseline:
    relative_orbit: int | None
    p10: float
    p50: float
    p90: float
    n: int
    first_year: int
    last_year: int
    threshold: float


def _sar_usable(p: SarPoint, rules: SarAlertRules) -> bool:
    return (
        p.acquired_on.month in rules.season_months
        and p.value is not None
        and bool(np.isfinite(p.value))
        and p.valid_fraction >= rules.min_valid_fraction
    )


def sar_baseline(
    history: Iterable[SarPoint], at: SarPoint, rules: SarAlertRules
) -> SarBaseline | None:
    """Same-season, same-relative-orbit baseline from usable acquisitions of earlier years."""
    past = [
        p
        for p in history
        if p.relative_orbit == at.relative_orbit
        and p.acquired_on.year < at.acquired_on.year
        and _sar_usable(p, rules)
    ]
    if len(past) < rules.min_history:
        return None
    values = np.array([p.value for p in past], dtype="float64")
    p10, p50, p90 = (float(v) for v in np.percentile(values, [10, 50, 90]))
    years = [p.acquired_on.year for p in past]
    return SarBaseline(
        relative_orbit=at.relative_orbit,
        p10=round(p10, 4),
        p50=round(p50, 4),
        p90=round(p90, 4),
        n=len(past),
        first_year=min(years),
        last_year=max(years),
        threshold=round(p50 - rules.drop_db, 4),
    )


def _sar_message(run: Sequence[tuple[SarPoint, SarBaseline]], rules: SarAlertRules) -> str:
    last = run[-1][1]
    months = ", ".join(calendar.month_abbr[m] for m in rules.season_months)
    seq = ", ".join(f"{p.acquired_on.isoformat()} {p.value:.1f} dB" for p, _ in run)
    return (
        f"Radar backscatter drop (heuristic): Sentinel-1 VH backscatter (polygon median, dB) was "
        f"more than {rules.drop_db:.1f} dB below this project's own same-season baseline on "
        f"{len(run)} consecutive acquisitions: {seq}. Latest threshold {last.threshold:.1f} dB = "
        f"median {last.p50:.1f} dB of {last.n} earlier {months} acquisitions "
        f"({last.first_year}–{last.last_year}, relative orbit {last.relative_orbit}) − "
        f"{rules.drop_db:.1f} dB. Canopy loss lowers VH, but so can flooding, soil moisture and "
        "tides: confirm with Sentinel-2 imagery or a field visit before acting. "
        "SAR alone never raises a high-severity alert."
    )


def detect_sar_backscatter_drop(
    points: Iterable[SarPoint],
    rules: SarAlertRules,
    *,
    since: date | None = None,
    existing: Collection[date] = (),
) -> list[AlertCandidate]:
    """`sar_backscatter_drop` candidates (see the rule above); `since`/`existing` as in
    `detect_vegetation_loss`."""
    if rules.consecutive < 1:
        raise ValueError("consecutive must be >= 1")
    usable = sorted(
        (p for p in points if _sar_usable(p, rules)), key=lambda p: (p.acquired_on, p.key)
    )
    out: list[AlertCandidate] = []
    run: list[tuple[SarPoint, SarBaseline]] = []
    for point in usable:
        base = sar_baseline(usable, point, rules)
        value = float(point.value) if point.value is not None else float("nan")
        if base is None or not value < base.threshold:
            run = []
            continue
        run.append((point, base))
        if len(run) != rules.consecutive:
            continue
        if (since is not None and point.acquired_on < since) or point.acquired_on in existing:
            continue
        out.append(
            AlertCandidate(
                kind=AlertKind.SAR_BACKSCATTER_DROP,
                metric=METRIC_VH_DB_P50,
                observed_on=point.acquired_on,
                observed_value=round(value, 4),
                severity=AlertSeverity.MEDIUM,
                expected={
                    "heuristic": True,
                    "confirmation": "optical (Sentinel-2) or field confirmation required",
                    "season_months": list(rules.season_months),
                    "relative_orbit": base.relative_orbit,
                    "p10": base.p10,
                    "p50": base.p50,
                    "p90": base.p90,
                    "n": base.n,
                    "years": [base.first_year, base.last_year],
                    "drop_db": rules.drop_db,
                    "threshold": base.threshold,
                    "consecutive": rules.consecutive,
                    "min_valid_fraction": rules.min_valid_fraction,
                    "observations": [
                        {
                            "date": p.acquired_on.isoformat(),
                            "value": p.value,
                            "valid_fraction": p.valid_fraction,
                            "acquisition_key": p.key,
                            "relative_orbit": p.relative_orbit,
                            "threshold": b.threshold,
                        }
                        for p, b in run
                    ],
                },
                message=_sar_message(run, rules),
                item_ids=[p.key for p, _ in run],
            )
        )
    return out
