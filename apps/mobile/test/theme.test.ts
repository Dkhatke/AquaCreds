import { describe, expect, it } from "vitest";
import { colors, palette, tones } from "../src/theme/tokens";

function luminance(hex: string): number {
  const n = Number.parseInt(hex.slice(1), 16);
  const channel = (c: number) => {
    const s = c / 255;
    return s <= 0.039_28 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return (
    0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255)
  );
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

const AA_TEXT = 4.5;

describe("WCAG 2.2 AA contrast (outdoor readability)", () => {
  it("body and muted text on every light surface", () => {
    for (const surface of [colors.background, colors.surface, colors.surfaceMuted]) {
      expect(contrast(colors.text, surface)).toBeGreaterThanOrEqual(AA_TEXT);
      expect(contrast(colors.textMuted, surface)).toBeGreaterThanOrEqual(AA_TEXT);
    }
  });

  it("status fills with their text, and status ink on light surfaces", () => {
    for (const [name, tone] of Object.entries(tones)) {
      expect(contrast(tone.text, tone.fill), name).toBeGreaterThanOrEqual(AA_TEXT);
      for (const surface of [colors.background, colors.surface]) {
        expect(contrast(tone.ink, surface), name).toBeGreaterThanOrEqual(AA_TEXT);
      }
    }
  });

  it("button and chrome pairs", () => {
    expect(contrast(colors.onPrimary, colors.primary)).toBeGreaterThanOrEqual(AA_TEXT);
    expect(contrast(colors.onDanger, colors.danger)).toBeGreaterThanOrEqual(AA_TEXT);
    expect(contrast(colors.onChrome, colors.chrome)).toBeGreaterThanOrEqual(AA_TEXT);
    expect(contrast(palette.failedOnDark, palette.deepOcean)).toBeGreaterThanOrEqual(AA_TEXT);
  });
});
