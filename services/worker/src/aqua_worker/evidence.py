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


@dataclass(frozen=True)
class Check:
    name: str
    outcome: Outcome
    reason: str
    hard: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    """`nonce` row as dict (or None). `used_by` = submission id that consumed it."""
    if nonce is None:
        return Check(
            "capture_nonce",
            "fail",
            "No valid capture nonce: photo not taken live in the app",
            hard=True,
        )
    if nonce["user_id"] != user_id or nonce["period_id"] != period_id:
        return Check(
            "capture_nonce", "fail", "Nonce was issued to another user or period", hard=True
        )
    if nonce.get("used_by") not in (None, submission_id):
        return Check("capture_nonce", "fail", "Nonce already used by another submission", hard=True)
    if captured_at < nonce["issued_at"] - NONCE_CLOCK_SKEW:
        return Check(
            "capture_nonce",
            "fail",
            "Photo claims a capture time before the nonce existed",
            hard=True,
        )
    if captured_at > nonce["expires_at"] + NONCE_CLOCK_SKEW:
        return Check("capture_nonce", "fail", "Capture happened after the nonce expired", hard=True)
    return Check("capture_nonce", "pass", "Captured live within the nonce window")


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


def not_yet_available(name: str, why: str) -> Check:
    return Check(name, "skipped", why)


def score(checks: list[Check]) -> tuple[int, bool]:
    """(trust score 0-100, rejected?). Hard failures reject; warnings cost WARN_PENALTY each."""
    if any(c.outcome == "fail" and c.hard for c in checks):
        return 0, True
    warns = sum(c.outcome == "warn" for c in checks)
    fails = sum(c.outcome == "fail" for c in checks)
    return max(0, 100 - WARN_PENALTY * warns - 40 * fails), False
