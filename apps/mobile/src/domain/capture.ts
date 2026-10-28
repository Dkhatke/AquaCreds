/**
 * Pure capture-time rules: nonce validity, phone clock skew, and GPS fix quality.
 *
 * The server rejects (hard fail) a photo whose `captured_at` is more than 60 s before the nonce
 * was issued or after it expired (worker `check_nonce`, NONCE_CLOCK_SKEW). `captured_at` comes from
 * the phone clock, so a phone whose clock is off by more than that will be rejected; we estimate
 * the offset from the HTTP `Date` header of the nonce response and warn.
 */

/** Stop using a nonce this long before it expires (the shutter + save takes a few seconds). */
export const NONCE_SAFETY_MS = 30_000;
/** Server tolerance for clock skew (`NONCE_CLOCK_SKEW` in the worker). */
export const SERVER_CLOCK_SKEW_MS = 60_000;
/** Warn when the estimated offset is above this. */
export const CLOCK_WARN_MS = 30_000;

/** API limit (`SubmissionCreate.gps_accuracy_m <= 500`). */
export const GPS_ACCURACY_MAX_M = 500;
export const GPS_GOOD_M = 10;
export const GPS_FAIR_M = 30;
/** A fix older than this is not "now". */
export const GPS_MAX_AGE_MS = 15_000;

export interface IssuedNonce {
  nonce: string;
  /** Server time (ISO 8601). */
  expiresAt: string;
  /** Estimated server-minus-device clock offset (ms) when it was issued; 0 if unknown. */
  clockOffsetMs: number;
}

/**
 * Server-minus-device clock offset from an HTTP `Date` header (1 s resolution), using the
 * midpoint of the request as the device time. Null when the header is missing or unparsable.
 */
export function clockOffsetFromDateHeader(
  dateHeader: string | null,
  requestStartMs: number,
  requestEndMs: number,
): number | null {
  if (!dateHeader) return null;
  const server = Date.parse(dateHeader);
  if (Number.isNaN(server)) return null;
  const offset = server - (requestStartMs + requestEndMs) / 2;
  // The header has 1 s resolution: anything below that is noise.
  return Math.abs(offset) < 1_000 ? 0 : Math.round(offset);
}

export function clockSkewWarning(offsetMs: number): boolean {
  return Math.abs(offsetMs) > CLOCK_WARN_MS;
}

/** Milliseconds of usable validity left (≤ 0 means request a new nonce). */
export function nonceRemainingMs(nonce: IssuedNonce, deviceNowMs: number): number {
  const serverNow = deviceNowMs + nonce.clockOffsetMs;
  return Date.parse(nonce.expiresAt) - NONCE_SAFETY_MS - serverNow;
}

export function nonceUsable(nonce: IssuedNonce | null, deviceNowMs: number): boolean {
  return nonce !== null && nonceRemainingMs(nonce, deviceNowMs) > 0;
}

export interface GpsFix {
  lat: number;
  lon: number;
  /** Reported horizontal accuracy (m, 68% radius on Android). */
  accuracyM: number | null;
  altitudeM: number | null;
  /** Fix time (ms since epoch). */
  timestamp: number;
  /** The OS says the position comes from a mock provider / spoofing tool. */
  mocked: boolean;
}

export type GpsQuality = "none" | "mocked" | "stale" | "unusable" | "poor" | "fair" | "good";

export function gpsQuality(fix: GpsFix | null, deviceNowMs: number): GpsQuality {
  if (!fix) return "none";
  // Evidence from a spoofed position is worthless; the API has no field to flag it, so block it.
  if (fix.mocked) return "mocked";
  if (deviceNowMs - fix.timestamp > GPS_MAX_AGE_MS) return "stale";
  if (fix.accuracyM === null || fix.accuracyM > GPS_ACCURACY_MAX_M) return "unusable";
  if (fix.accuracyM > GPS_FAIR_M) return "poor";
  if (fix.accuracyM > GPS_GOOD_M) return "fair";
  return "good";
}

/** Shooting is allowed with a fresh fix the API will accept (poor fixes warn but are allowed). */
export function canShoot(quality: GpsQuality): boolean {
  return quality === "poor" || quality === "fair" || quality === "good";
}

/** Normalise a compass heading to the API range [0, 360), or null. */
export function normaliseHeading(deg: number | null | undefined): number | null {
  if (deg === null || deg === undefined || !Number.isFinite(deg) || deg < 0) return null;
  const h = deg % 360;
  return h >= 360 ? 0 : h;
}

/**
 * EXIF tags written into the JPEG by expo-camera (`additionalExif`). The GPS position duplicates
 * the app-reported fix: it is a consistency check, not independent evidence (see README).
 */
export function additionalExifFor(fix: GpsFix): Record<string, number | string> {
  const exif: Record<string, number | string> = {
    GPSLatitude: fix.lat,
    GPSLongitude: fix.lon,
    GPSLatitudeRef: fix.lat >= 0 ? "N" : "S",
    GPSLongitudeRef: fix.lon >= 0 ? "E" : "W",
  };
  if (fix.altitudeM !== null) exif["GPSAltitude"] = fix.altitudeM;
  return exif;
}
