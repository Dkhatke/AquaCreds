"""Evidence checks for field photos (pure functions; DB/storage access lives in tasks/evidence.py).

Each check yields pass | warn | fail | skipped with a human-readable reason. A *hard* failure
(outside the boundary, exact duplicate, invalid capture nonce) rejects the submission; everything
else is advisory and shown to the verifier as a trust score with reasons. Nothing here claims more
than it can prove: e.g. EXIF can be edited, so EXIF mismatches only warn.
"""

import io
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import imagehash
from PIL import ExifTags, Image

Outcome = Literal["pass", "warn", "fail", "skipped"]

# Thresholds (documented so verifiers can see what "warn" means)
EXIF_DISTANCE_MIN_M = 50.0  # tolerance floor between EXIF GPS and app GPS
EXIF_DISTANCE_ACCURACY_FACTOR = 3.0
EXIF_TIME_TOLERANCE = timedelta(minutes=10)
NONCE_CLOCK_SKEW = timedelta(seconds=60)
GEOFENCE_EXTRA_M = 10.0  # on top of the reported GPS accuracy
PHASH_DUPLICATE_MAX = 6  # Hamming distance (64-bit pHash): near-identical image
PHASH_SIMILAR_MAX = 10
WARN_PENALTY = 20
# A pre-issued (batch) nonce only proves the photo was taken after the batch was issued, up to
# CAPTURE_NONCE_BATCH_TTL_H (72 h) earlier, not minutes before the shot. It still binds the
# photo to this agent + period and is single-use, so a valid one is a *lower-weight* warning:
# half the standard penalty (100 → 90), visible to the verifier as `nonce_mode: batch`.
BATCH_NONCE_WEIGHT = 0.5


@dataclass(frozen=True)
class Check:
    name: str
    outcome: Outcome
    reason: str
    hard: bool = False
    details: dict[str, Any] | None = None  # machine-readable evidence (e.g. model outputs)
    weight: float = 1.0  # share of WARN_PENALTY a warning costs

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        if self.details is None:
            del out["details"]
        if self.weight == 1.0:
            del out["weight"]
        return out


# ---------------------------------------------------------------- primitives


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def perceptual_hash(image_bytes: bytes) -> str:
    with Image.open(io.BytesIO(image_bytes)) as img:
        return str(imagehash.phash(img.convert("RGB")))


def hamming_hex(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def _dms_to_deg(dms: Any, ref: str) -> float:
    deg = float(dms[0]) + float(dms[1]) / 60 + float(dms[2]) / 3600
    return -deg if ref in ("S", "W") else deg


def read_exif(image_bytes: bytes) -> dict[str, Any]:
    """GPS position, capture time (with UTC offset if recorded) and camera model, when present."""
    out: dict[str, Any] = {}
    with Image.open(io.BytesIO(image_bytes)) as img:
        exif = img.getexif()
        if not exif:
            return out
        out["make"] = str(exif.get(ExifTags.Base.Make, "")).strip("\x00 ") or None
        out["model"] = str(exif.get(ExifTags.Base.Model, "")).strip("\x00 ") or None
        out["software"] = str(exif.get(ExifTags.Base.Software, "")).strip("\x00 ") or None
        sub = exif.get_ifd(ExifTags.IFD.Exif)
        dto = sub.get(ExifTags.Base.DateTimeOriginal)
        offset = sub.get(ExifTags.Base.OffsetTimeOriginal)
        if dto:
            out["datetime_original"] = str(dto)
            out["offset_time_original"] = str(offset) if offset else None
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
        try:
            out["lat"] = _dms_to_deg(
                gps[ExifTags.GPS.GPSLatitude], gps[ExifTags.GPS.GPSLatitudeRef]
            )
            out["lon"] = _dms_to_deg(
                gps[ExifTags.GPS.GPSLongitude], gps[ExifTags.GPS.GPSLongitudeRef]
            )
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            pass
    return {k: v for k, v in out.items() if v is not None}


def exif_capture_time(exif: dict[str, Any]) -> tuple[datetime | None, bool]:
    """(time, has_timezone). EXIF times are local unless OffsetTimeOriginal is present."""
    raw = exif.get("datetime_original")
    if not raw:
        return None, False
    try:
        naive = datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None, False
    offset = exif.get("offset_time_original")
    if offset:
        try:
            aware = datetime.strptime(f"{raw} {offset}", "%Y:%m:%d %H:%M:%S %z")
            return aware.astimezone(UTC), True
        except ValueError:
            pass
    return naive, False


# ---------------------------------------------------------------- checks


def check_exif(
    exif: dict[str, Any], lat: float, lon: float, accuracy_m: float, captured_at: datetime
) -> list[Check]:
    checks: list[Check] = []
    if "lat" not in exif:
        checks.append(Check("exif_gps", "warn", "Photo has no EXIF GPS (stripped or disabled)"))
    else:
        dist = haversine_m(lat, lon, exif["lat"], exif["lon"])
        limit = max(EXIF_DISTANCE_MIN_M, EXIF_DISTANCE_ACCURACY_FACTOR * accuracy_m)
        if dist > limit:
            checks.append(
                Check(
                    "exif_gps",
                    "warn",
                    f"EXIF GPS is {dist:.0f} m from app GPS (limit {limit:.0f} m)",
                )
            )
        else:
            checks.append(Check("exif_gps", "pass", f"EXIF GPS within {dist:.0f} m of app GPS"))

    when, has_tz = exif_capture_time(exif)
    if when is None:
        checks.append(Check("exif_time", "warn", "Photo has no EXIF capture time"))
    elif has_tz:
        delta = abs(when - captured_at.astimezone(UTC))
        if delta > EXIF_TIME_TOLERANCE:
            checks.append(Check("exif_time", "warn", f"EXIF time differs from app time by {delta}"))
        else:
            checks.append(Check("exif_time", "pass", "EXIF time matches app time"))
    else:
        # Local time without zone: only the date can be compared safely (±1 day for timezones).
        if abs((when.date() - captured_at.date()).days) > 1:
            checks.append(
                Check("exif_time", "warn", f"EXIF date {when.date()} far from capture date")
            )
        else:
            checks.append(Check("exif_time", "pass", "EXIF date consistent (no timezone recorded)"))
    return checks


def check_nonce(
    nonce: dict[str, Any] | None,
    *,
    user_id: str,
    period_id: str,
    captured_at: datetime,
    submission_id: str,
) -> Check:
    """`nonce` row as dict (or None). `used_by` = submission id that consumed it; `mode` = "live"
    (default) or "batch" (pre-issued for offline capture).

    The window rules are the same for both modes (captured after issue, before expiry, ± clock
    skew) and any violation is a hard failure. A valid live nonce passes; a valid batch nonce is a
    lower-weight warning (`BATCH_NONCE_WEIGHT`), because its window is days, not minutes."""
    if nonce is None:
        return Check(
            "capture_nonce",
            "fail",
            "No valid capture nonce: photo not taken live in the app",
            hard=True,
        )
    mode = str(nonce.get("mode") or "live")
    details = {"nonce_mode": mode}
    if nonce["user_id"] != user_id or nonce["period_id"] != period_id:
        return Check(
            "capture_nonce",
            "fail",
            "Nonce was issued to another user or period",
            hard=True,
            details=details,
        )
    if nonce.get("used_by") not in (None, submission_id):
        return Check(
            "capture_nonce",
            "fail",
            "Nonce already used by another submission",
            hard=True,
            details=details,
        )
    if captured_at < nonce["issued_at"] - NONCE_CLOCK_SKEW:
        return Check(
            "capture_nonce",
            "fail",
            "Photo claims a capture time before the nonce existed",
            hard=True,
            details=details,
        )
    if captured_at > nonce["expires_at"] + NONCE_CLOCK_SKEW:
        return Check(
            "capture_nonce",
            "fail",
            "Capture happened after the nonce expired",
            hard=True,
            details=details,
        )
    if mode == "batch":
        window_h = (nonce["expires_at"] - nonce["issued_at"]).total_seconds() / 3600
        return Check(
            "capture_nonce",
            "warn",
            f"Captured with a pre-issued offline nonce: proves only that the photo was taken "
            f"after {nonce['issued_at']:%Y-%m-%d %H:%M} UTC (window {window_h:.0f} h)",
            details={**details, "window_h": round(window_h, 1)},
            weight=BATCH_NONCE_WEIGHT,
        )
    return Check("capture_nonce", "pass", "Captured live within the nonce window", details=details)


def check_geofence(distance_outside_m: float, accuracy_m: float) -> Check:
    """`distance_outside_m` = 0 when the point is inside the project boundary."""
    if distance_outside_m <= 0:
        return Check("geofence", "pass", "Inside the project boundary")
    if distance_outside_m <= accuracy_m + GEOFENCE_EXTRA_M:
        return Check(
            "geofence",
            "warn",
            f"{distance_outside_m:.0f} m outside the boundary, within GPS accuracy (±{accuracy_m:.0f} m)",
        )
    return Check(
        "geofence", "fail", f"{distance_outside_m:.0f} m outside the project boundary", hard=True
    )


def check_duplicates(exact_matches: int, nearest_phash_distance: int | None) -> list[Check]:
    checks = []
    if exact_matches:
        checks.append(
            Check(
                "duplicate_exact",
                "fail",
                f"Identical file already submitted ({exact_matches} times)",
                hard=True,
            )
        )
    else:
        checks.append(Check("duplicate_exact", "pass", "No identical file on the platform"))
    if nearest_phash_distance is None or nearest_phash_distance > PHASH_SIMILAR_MAX:
        checks.append(Check("duplicate_visual", "pass", "No visually similar photo found"))
    elif nearest_phash_distance <= PHASH_DUPLICATE_MAX:
        checks.append(
            Check(
                "duplicate_visual",
                "fail",
                f"Near-identical photo already submitted (pHash distance {nearest_phash_distance})",
                hard=True,
            )
        )
    else:
        checks.append(
            Check(
                "duplicate_visual",
                "warn",
                f"Similar photo exists (pHash distance {nearest_phash_distance})",
            )
        )
    return checks


def check_photo_model(result: dict[str, Any], pass_at: float, low: float) -> Check:
    """Photo-verifier outcome (advisory; never hard, so it can never reject a submission alone).

    `result` is `aqua_worker.photo_verifier.photo_plausibility(...)`. Bands, fixed per model
    version in its labels.json and justified in ml/model_cards/photo_verifier_v1.md:
    P(mangrove) ≥ pass_at → pass; low ≤ P < pass_at → warn (uncertain); P < low → warn (unlikely).
    Both warnings cost the standard WARN_PENALTY; a verifier looks at the photo either way.
    """
    p = float(result["mangrove_prob"])
    top = result.get("top_genera") or []
    genus = f"; most likely genus {top[0]['genus']}" if top and p >= pass_at else ""
    version = result.get("model_version", "?")
    if p >= pass_at:
        return Check(
            "photo_model",
            "pass",
            f"Photo looks like mangrove vegetation (p={p:.2f}{genus}; {version})",
            details=result,
        )
    if p >= low:
        return Check(
            "photo_model",
            "warn",
            f"Unsure whether the photo shows mangroves (p={p:.2f}; {version}): check it visually",
            details=result,
        )
    return Check(
        "photo_model",
        "warn",
        f"Photo does not look like mangrove vegetation (p={p:.2f}; {version}): check it visually",
        details=result,
    )


def not_yet_available(name: str, why: str) -> Check:
    return Check(name, "skipped", why)


def score(checks: list[Check]) -> tuple[int, bool]:
    """(trust score 0-100, rejected?). Hard failures reject; warnings cost WARN_PENALTY each,
    scaled by the check's weight (a batch-nonce warning costs half)."""
    if any(c.outcome == "fail" and c.hard for c in checks):
        return 0, True
    warn_penalty = sum(WARN_PENALTY * c.weight for c in checks if c.outcome == "warn")
    fails = sum(c.outcome == "fail" for c in checks)
    return max(0, round(100 - warn_penalty - 40 * fails)), False
