from datetime import UTC, datetime, timedelta

import pytest
from photo_factory import make_jpeg

from aqua_worker import evidence as ev

NOW = datetime(2026, 10, 19, 5, 0, tzinfo=UTC)  # 10:30 IST


def test_haversine_known_distance() -> None:
    # 0.01° of latitude ≈ 1,112 m
    assert ev.haversine_m(20.70, 86.85, 20.71, 86.85) == pytest.approx(1112, rel=0.01)


def test_read_exif_roundtrip_gps_time_and_camera() -> None:
    data = make_jpeg(lat=20.7012, lon=86.8634, when="2026:10:19 10:30:00", offset="+05:30")
    exif = ev.read_exif(data)
    assert exif["lat"] == pytest.approx(20.7012, abs=1e-4)
    assert exif["lon"] == pytest.approx(86.8634, abs=1e-4)
    assert exif["make"] == "TestCam"
    when, has_tz = ev.exif_capture_time(exif)
    assert has_tz
    assert when == NOW


def test_read_exif_without_metadata() -> None:
    assert "lat" not in ev.read_exif(make_jpeg())


def test_exif_consistent_photo_passes() -> None:
    exif = ev.read_exif(
        make_jpeg(lat=20.7012, lon=86.8634, when="2026:10:19 10:31:00", offset="+05:30")
    )
    checks = ev.check_exif(exif, 20.7013, 86.8635, 5.0, NOW)
    assert [c.outcome for c in checks] == ["pass", "pass"]


def test_exif_mismatch_only_warns() -> None:
    exif = ev.read_exif(
        make_jpeg(lat=20.80, lon=86.95, when="2026:10:17 09:00:00", offset="+05:30")
    )
    checks = {c.name: c for c in ev.check_exif(exif, 20.70, 86.85, 5.0, NOW)}
    assert checks["exif_gps"].outcome == "warn"
    assert "m from app GPS" in checks["exif_gps"].reason
    assert checks["exif_time"].outcome == "warn"
    assert not any(c.hard for c in checks.values())  # EXIF is editable: never a hard failure


def test_exif_local_time_without_zone_compares_date_only() -> None:
    exif = ev.read_exif(make_jpeg(when="2026:10:19 10:30:00"))
    (gps, time) = ev.check_exif(exif, 20.70, 86.85, 5.0, NOW)
    assert gps.outcome == "warn"
    assert time.outcome == "pass"
    assert "no timezone" in time.reason


def _nonce(**over: object) -> dict[str, object]:
    base = {
        "user_id": "u1",
        "period_id": "p1",
        "issued_at": NOW - timedelta(minutes=2),
        "expires_at": NOW + timedelta(minutes=13),
        "used_by": "s1",
    }
    return base | over


@pytest.mark.parametrize(
    ("nonce", "captured", "outcome"),
    [
        (_nonce(), NOW, "pass"),
        (None, NOW, "fail"),
        (_nonce(user_id="u2"), NOW, "fail"),
        (_nonce(used_by="s9"), NOW, "fail"),
        (_nonce(), NOW - timedelta(hours=1), "fail"),  # gallery photo taken before the nonce
        (_nonce(), NOW + timedelta(hours=1), "fail"),  # captured after expiry
        (_nonce(), NOW - timedelta(minutes=2, seconds=30), "pass"),  # within clock-skew allowance
    ],
)
def test_nonce_rules(nonce: dict[str, object] | None, captured: datetime, outcome: str) -> None:
    check = ev.check_nonce(
        nonce, user_id="u1", period_id="p1", captured_at=captured, submission_id="s1"
    )
    assert check.outcome == outcome
    assert check.hard == (outcome == "fail")


def _batch(**over: object) -> dict[str, object]:
    base = _nonce(
        mode="batch", issued_at=NOW - timedelta(hours=30), expires_at=NOW + timedelta(hours=42)
    )
    return base | over


def test_batch_nonce_is_a_lower_weight_warning() -> None:
    check = ev.check_nonce(
        _batch(), user_id="u1", period_id="p1", captured_at=NOW, submission_id="s1"
    )
    assert (check.outcome, check.hard, check.weight) == ("warn", False, ev.BATCH_NONCE_WEIGHT)
    assert check.details == {"nonce_mode": "batch", "window_h": 72.0}
    assert "pre-issued offline nonce" in check.reason
    out = check.to_dict()
    assert out["weight"] == 0.5
    assert out["details"]["nonce_mode"] == "batch"
    assert ev.score([check, ev.Check("a", "pass", "")]) == (90, False)

    live = ev.check_nonce(
        _nonce(), user_id="u1", period_id="p1", captured_at=NOW, submission_id="s1"
    )
    assert live.details == {"nonce_mode": "live"}
    assert "weight" not in live.to_dict()


@pytest.mark.parametrize(
    ("nonce", "captured"),
    [
        (_batch(), NOW - timedelta(hours=31)),  # before the batch was issued
        (_batch(), NOW + timedelta(hours=43)),  # after the 72 h window
        (_batch(used_by="s9"), NOW),  # single use
        (_batch(user_id="u2"), NOW),  # bound to the agent
        (_batch(period_id="p2"), NOW),  # ... and the period
    ],
)
def test_batch_nonce_window_and_binding_are_hard(
    nonce: dict[str, object], captured: datetime
) -> None:
    check = ev.check_nonce(
        nonce, user_id="u1", period_id="p1", captured_at=captured, submission_id="s1"
    )
    assert (check.outcome, check.hard) == ("fail", True)
    assert check.details == {"nonce_mode": "batch"}


def test_geofence_uses_gps_accuracy_buffer() -> None:
    assert ev.check_geofence(0.0, 5.0).outcome == "pass"
    assert ev.check_geofence(12.0, 5.0).outcome == "warn"  # within 5 m accuracy + 10 m
    outside = ev.check_geofence(250.0, 5.0)
    assert outside.outcome == "fail"
    assert outside.hard


def test_perceptual_hash_detects_reencoded_copy() -> None:
    original = ev.perceptual_hash(make_jpeg(seed=1))
    brighter = ev.perceptual_hash(make_jpeg(seed=1, brightness=1.08))
    other = ev.perceptual_hash(make_jpeg(seed=2))
    assert ev.hamming_hex(original, brighter) <= ev.PHASH_DUPLICATE_MAX
    assert ev.hamming_hex(original, other) > ev.PHASH_SIMILAR_MAX


def test_duplicate_rules() -> None:
    exact = ev.check_duplicates(1, 0)
    assert exact[0].outcome == "fail"
    assert exact[0].hard
    assert [c.outcome for c in ev.check_duplicates(0, 3)] == ["pass", "fail"]
    assert [c.outcome for c in ev.check_duplicates(0, 9)] == ["pass", "warn"]
    assert [c.outcome for c in ev.check_duplicates(0, None)] == ["pass", "pass"]


def test_score() -> None:
    ok = [ev.Check("a", "pass", ""), ev.Check("b", "skipped", "")]
    assert ev.score(ok) == (100, False)
    assert ev.score([*ok, ev.Check("c", "warn", ""), ev.Check("d", "warn", "")]) == (60, False)
    assert ev.score([*ok, ev.Check("e", "fail", "", hard=True)]) == (0, True)
