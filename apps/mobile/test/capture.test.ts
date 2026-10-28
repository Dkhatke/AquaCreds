import { describe, expect, it } from "vitest";
import {
  additionalExifFor,
  canShoot,
  clockOffsetFromDateHeader,
  clockSkewWarning,
  type GpsFix,
  gpsQuality,
  NONCE_SAFETY_MS,
  nonceRemainingMs,
  nonceUsable,
  normaliseHeading,
} from "../src/domain/capture";

const NOW = Date.parse("2026-10-28T06:00:00Z");

describe("nonce validity", () => {
  const nonce = { nonce: "x", expiresAt: "2026-10-28T06:15:00Z", clockOffsetMs: 0 };

  it("keeps a safety margin before expiry", () => {
    expect(nonceRemainingMs(nonce, NOW)).toBe(15 * 60_000 - NONCE_SAFETY_MS);
    expect(nonceUsable(nonce, NOW + 14 * 60_000)).toBe(true);
    expect(nonceUsable(nonce, NOW + 14.5 * 60_000)).toBe(false);
    expect(nonceUsable(null, NOW)).toBe(false);
  });

  it("judges expiry on the server clock when the phone clock is off", () => {
    // Phone is 5 min behind the server: by the phone's clock 06:10 is already 06:15 server time.
    const behind = { ...nonce, clockOffsetMs: 5 * 60_000 };
    expect(nonceUsable(behind, NOW + 10 * 60_000)).toBe(false);
  });
});

describe("clock offset from the HTTP Date header", () => {
  it("compares the header with the request midpoint", () => {
    const header = new Date(NOW + 120_000).toUTCString();
    expect(clockOffsetFromDateHeader(header, NOW - 500, NOW + 500)).toBe(120_000);
    expect(clockOffsetFromDateHeader(new Date(NOW).toUTCString(), NOW, NOW + 400)).toBe(0);
    expect(clockOffsetFromDateHeader(null, NOW, NOW)).toBeNull();
    expect(clockOffsetFromDateHeader("garbage", NOW, NOW)).toBeNull();
  });

  it("warns beyond 30 s (the server tolerates 60 s)", () => {
    expect(clockSkewWarning(29_000)).toBe(false);
    expect(clockSkewWarning(-31_000)).toBe(true);
  });
});

describe("GPS quality", () => {
  const fix = (accuracyM: number | null, ageMs = 0): GpsFix => ({
    lat: 20.7,
    lon: 86.9,
    accuracyM,
    altitudeM: null,
    timestamp: NOW - ageMs,
  });

  it("grades fixes and blocks unusable ones", () => {
    expect(gpsQuality(null, NOW)).toBe("none");
    expect(gpsQuality(fix(5, 20_000), NOW)).toBe("stale");
    expect(gpsQuality(fix(null), NOW)).toBe("unusable");
    expect(gpsQuality(fix(501), NOW)).toBe("unusable");
    expect(gpsQuality(fix(31), NOW)).toBe("poor");
    expect(gpsQuality(fix(30), NOW)).toBe("fair");
    expect(gpsQuality(fix(10), NOW)).toBe("good");
    expect(canShoot("poor")).toBe(true);
    expect(canShoot("stale")).toBe(false);
    expect(canShoot("unusable")).toBe(false);
  });
});

describe("heading and EXIF", () => {
  it("normalises headings to [0, 360)", () => {
    expect(normaliseHeading(359.9)).toBe(359.9);
    expect(normaliseHeading(360)).toBe(0);
    expect(normaliseHeading(-1)).toBeNull(); // platform "unknown"
    expect(normaliseHeading(undefined)).toBeNull();
  });

  it("writes signed GPS with explicit refs", () => {
    expect(
      additionalExifFor({ lat: -8.5, lon: 86.9, accuracyM: 3, altitudeM: 2, timestamp: NOW }),
    ).toEqual({
      GPSLatitude: -8.5,
      GPSLongitude: 86.9,
      GPSLatitudeRef: "S",
      GPSLongitudeRef: "E",
      GPSAltitude: 2,
    });
  });
});
