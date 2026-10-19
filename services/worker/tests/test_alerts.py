from datetime import date

import pytest

from aqua_core.models import AlertKind, AlertSeverity
from aqua_worker.alerts import (
    AlertRules,
    Observation,
    detect_vegetation_loss,
    envelope,
)


def _history(
    month: int = 1, years: range = range(2019, 2024), level: float = 0.75
) -> list[Observation]:
    """Three clear observations per year in `month`, around `level`."""
    out = []
    for year in years:
        for i, day in enumerate((5, 15, 25)):
            out.append(
                Observation(date(year, month, day), 0.9, level + 0.01 * (i - 1), f"h{year}{i}")
            )
    return out


def obs(day: str, value: float | None, clear: float = 0.9) -> Observation:
    return Observation(date.fromisoformat(day), clear, value, f"S2_{day}")


def test_envelope_uses_same_month_of_earlier_years_only() -> None:
    history = _history(1) + _history(7, level=0.30) + [obs("2024-01-02", 0.10)]
    env = envelope(history, obs("2024-01-20", 0.5), AlertRules())
    assert env is not None
    assert env.month == 1
    assert env.n == 15  # five Januaries x 3; July and 2024 values excluded
    assert env.p10 == pytest.approx(0.74, abs=0.002)
    assert env.threshold == pytest.approx(env.p10 - 0.05)
    assert (env.first_year, env.last_year) == (2019, 2023)


def test_envelope_needs_minimum_history() -> None:
    assert (
        envelope(_history(years=range(2023, 2024))[:2], obs("2024-01-20", 0.5), AlertRules())
        is None
    )


def test_single_low_observation_does_not_alert() -> None:
    assert detect_vegetation_loss([*_history(), obs("2024-01-10", 0.40)]) == []


def test_two_consecutive_clear_lows_alert_once_and_explain() -> None:
    alerts = detect_vegetation_loss([*_history(), obs("2024-01-10", 0.40), obs("2024-01-20", 0.42)])
    assert len(alerts) == 1
    a = alerts[0]
    assert a.kind == AlertKind.VEGETATION_LOSS
    assert a.metric == "ndvi_p50"
    assert a.observed_on == date(2024, 1, 20)
    assert a.observed_value == pytest.approx(0.42)
    assert a.severity == AlertSeverity.HIGH  # ~46% below the January median
    assert a.item_ids == ["S2_2024-01-10", "S2_2024-01-20"]
    assert [o["date"] for o in a.expected["observations"]] == ["2024-01-10", "2024-01-20"]
    assert a.expected["n"] == 15
    assert "2 consecutive clear observations" in a.message
    assert "January P10" in a.message
    assert "verify on the ground" in a.message


def test_cloudy_scenes_are_ignored_not_breaking_the_run() -> None:
    series = [
        *_history(),
        obs("2024-01-05", 0.40),
        obs("2024-01-10", 0.95, clear=0.3),  # cloudy: neither confirms nor clears
        obs("2024-01-15", None),  # no valid pixels
        obs("2024-01-20", 0.41),
    ]
    assert [a.observed_on for a in detect_vegetation_loss(series)] == [date(2024, 1, 20)]


def test_a_normal_clear_observation_resets_the_run() -> None:
    series = [
        *_history(),
        obs("2024-01-05", 0.40),
        obs("2024-01-10", 0.76),
        obs("2024-01-20", 0.41),
    ]
    assert detect_vegetation_loss(series) == []


def test_long_event_alerts_once_and_a_new_event_alerts_again() -> None:
    lows = [obs(f"2024-01-{d:02d}", 0.40) for d in (3, 8, 13, 18)]
    recovery = obs("2024-01-23", 0.75)
    again = [obs("2024-01-26", 0.40), obs("2024-01-29", 0.40)]
    alerts = detect_vegetation_loss([*_history(), *lows, recovery, *again])
    assert [a.observed_on for a in alerts] == [date(2024, 1, 8), date(2024, 1, 29)]


def test_seasonal_low_is_compared_with_its_own_month() -> None:
    # July is always low here (monsoon/tide); a low July is not loss.
    series = [
        *_history(1),
        *_history(7, level=0.30),
        obs("2024-07-05", 0.29),
        obs("2024-07-15", 0.28),
    ]
    assert detect_vegetation_loss(series) == []


def test_no_history_means_no_alert() -> None:
    assert detect_vegetation_loss([obs("2024-01-05", 0.1), obs("2024-01-10", 0.1)]) == []


def test_since_and_existing_prevent_duplicates() -> None:
    series = [*_history(), obs("2024-01-10", 0.40), obs("2024-01-20", 0.42)]
    assert detect_vegetation_loss(series, since=date(2024, 1, 21)) == []
    assert detect_vegetation_loss(series, since=date(2024, 1, 20)) != []
    assert detect_vegetation_loss(series, existing={date(2024, 1, 20)}) == []


@pytest.mark.parametrize(
    ("value", "severity"),
    [(0.66, AlertSeverity.LOW), (0.55, AlertSeverity.MEDIUM), (0.30, AlertSeverity.HIGH)],
)
def test_severity_scales_with_drop_below_median(value: float, severity: AlertSeverity) -> None:
    alerts = detect_vegetation_loss(
        [*_history(), obs("2024-01-10", value), obs("2024-01-20", value)]
    )
    assert [a.severity for a in alerts] == [severity]


def test_rules_are_configurable() -> None:
    series = [*_history(), obs("2024-01-10", 0.40)]
    assert len(detect_vegetation_loss(series, AlertRules(consecutive=1))) == 1
    with pytest.raises(ValueError, match="consecutive"):
        detect_vegetation_loss(series, AlertRules(consecutive=0))
