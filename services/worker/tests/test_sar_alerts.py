"""Sentinel-1 `sar_backscatter_drop` heuristic on synthetic VH dB series (pure functions)."""

from datetime import date
from types import SimpleNamespace

import pytest

from aqua_core.models import AlertKind, AlertSeverity
from aqua_worker.alerts import (
    METRIC_VH_DB_P50,
    SarAlertRules,
    SarPoint,
    detect_sar_backscatter_drop,
    sar_baseline,
)

RULES = SarAlertRules()  # 3 dB, 3 consecutive, 6 history, valid >= 0.6, Jun-Sep


def p(day: str, vh: float | None, valid: float = 0.95, orbit: int | None = 85) -> SarPoint:
    return SarPoint(date.fromisoformat(day), valid, vh, f"S1A_{day}", orbit)


def history(years: range = range(2019, 2024), level: float = -15.0, orbit: int = 85) -> list:
    """Four healthy monsoon passes per year (Jun-Sep) plus one dry-season pass."""
    out = []
    for year in years:
        for i, md in enumerate(("06-10", "07-04", "08-09", "09-14")):
            out.append(p(f"{year}-{md}", level + 0.2 * (i - 1.5), orbit=orbit))
        out.append(p(f"{year}-01-15", level - 8.0, orbit=orbit))  # dry season: ignored
    return out


def test_baseline_uses_same_season_same_orbit_earlier_years() -> None:
    hist = history() + history(level=-25.0, orbit=12) + [p("2024-06-02", -30.0)]
    base = sar_baseline(hist, p("2024-07-01", -20.0), RULES)
    assert base is not None
    assert base.n == 20  # 5 years x 4 monsoon passes on orbit 85; orbit 12, Jan, 2024 excluded
    assert base.p50 == pytest.approx(-15.0, abs=0.01)
    assert base.threshold == pytest.approx(base.p50 - 3.0)
    assert (base.first_year, base.last_year, base.relative_orbit) == (2019, 2023, 85)
    assert sar_baseline(history(range(2022, 2023)), p("2024-07-01", -20.0), RULES) is None


def test_sustained_drop_fires_one_medium_alert() -> None:
    drop = [p("2024-06-12", -19.0), p("2024-06-24", -19.5), p("2024-07-06", -19.2)]
    later = [p("2024-07-18", -19.4)]  # same event continues: no second alert
    out = detect_sar_backscatter_drop(history() + drop + later, RULES)
    assert len(out) == 1
    a = out[0]
    assert (a.kind, a.metric, a.severity) == (
        AlertKind.SAR_BACKSCATTER_DROP,
        METRIC_VH_DB_P50,
        AlertSeverity.MEDIUM,
    )
    assert a.observed_on == date(2024, 7, 6)
    assert a.observed_value == -19.2
    assert a.expected["heuristic"] is True
    assert "confirmation" in a.expected
    assert [o["acquisition_key"] for o in a.expected["observations"]] == [
        "S1A_2024-06-12",
        "S1A_2024-06-24",
        "S1A_2024-07-06",
    ]
    assert "Sentinel-2" in a.message
    assert "field visit" in a.message
    assert a.item_ids == ["S1A_2024-06-12", "S1A_2024-06-24", "S1A_2024-07-06"]


def test_severity_is_never_high_even_for_a_huge_drop() -> None:
    drop = [p(d, -35.0) for d in ("2024-06-12", "2024-06-24", "2024-07-06")]
    out = detect_sar_backscatter_drop(history() + drop, RULES)
    assert [a.severity for a in out] == [AlertSeverity.MEDIUM]


@pytest.mark.parametrize(
    "series",
    [
        # short dip: only two passes below, then recovery
        [p("2024-06-12", -19.0), p("2024-06-24", -19.0), p("2024-07-06", -15.0)],
        # within the 3 dB margin
        [p(d, -17.5) for d in ("2024-06-12", "2024-06-24", "2024-07-06")],
        # low but mostly invalid pixels (valid fraction below 0.6)
        [p(d, -20.0, valid=0.3) for d in ("2024-06-12", "2024-06-24", "2024-07-06")],
        # dry-season passes are not evaluated by the monsoon heuristic
        [p(d, -25.0) for d in ("2024-01-12", "2024-01-24", "2024-02-05")],
        # another relative orbit has no baseline of its own
        [p(d, -20.0, orbit=12) for d in ("2024-06-12", "2024-06-24", "2024-07-06")],
        # missing values
        [p(d, None) for d in ("2024-06-12", "2024-06-24", "2024-07-06")],
    ],
)
def test_heuristic_does_not_fire(series: list[SarPoint]) -> None:
    assert detect_sar_backscatter_drop(history() + series, RULES) == []


def test_since_existing_and_validation() -> None:
    drop = [p("2024-06-12", -19.0), p("2024-06-24", -19.5), p("2024-07-06", -19.2)]
    points = history() + drop
    assert detect_sar_backscatter_drop(points, RULES, since=date(2024, 7, 7)) == []
    assert detect_sar_backscatter_drop(points, RULES, existing={date(2024, 7, 6)}) == []
    with pytest.raises(ValueError, match="consecutive"):
        detect_sar_backscatter_drop(points, SarAlertRules(consecutive=0))
    looser = SarAlertRules(drop_db=1.0, consecutive=1, min_history=2)
    assert len(detect_sar_backscatter_drop(points, looser)) == 1


def test_rules_from_settings_default_off() -> None:
    base = {
        "geo_s1_enabled": True,
        "geo_sar_alerts_enabled": False,
        "geo_sar_alert_drop_db": 4.0,
        "geo_sar_alert_consecutive": 2,
        "geo_sar_alert_min_history": 8,
        "geo_sar_alert_min_valid_fraction": 0.7,
        "geo_s1_monsoon_months": [7, 8],
    }
    assert SarAlertRules.from_settings(SimpleNamespace(**base)) is None
    on = SarAlertRules.from_settings(SimpleNamespace(**{**base, "geo_sar_alerts_enabled": True}))
    assert on == SarAlertRules(4.0, 2, 8, 0.7, (7, 8))
    s1_off = {**base, "geo_sar_alerts_enabled": True, "geo_s1_enabled": False}
    assert SarAlertRules.from_settings(SimpleNamespace(**s1_off)) is None


def test_settings_defaults() -> None:
    from aqua_core.settings import Settings

    s = Settings(_env_file=None)
    assert s.geo_sar_alerts_enabled is False
    assert SarAlertRules.from_settings(s) is None
    assert (s.geo_sar_alert_drop_db, s.geo_sar_alert_consecutive) == (3.0, 3)
