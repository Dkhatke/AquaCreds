/**
 * AquaCreds design tokens for the field app (docs/design/DESIGN_DIRECTION.md §1).
 *
 * The field app uses LIGHT surfaces only: phones are read in direct sun on mudflats. Brand colours
 * that are too light to be read as text on a light surface (lagoon-mint, review-amber) are used as
 * fills with `deepOcean` text on top; their `*Ink` variants are the text-on-light versions.
 * Contrast is asserted in test/theme.test.ts (WCAG 2.2 AA).
 */
export const palette = {
  deepOcean: "#0B2024",
  mangrove: "#103A38",
  lagoonMint: "#43C9A4",
  seaMist: "#E9F0EC",
  offWhite: "#F8FAF9",
  reviewAmber: "#E9AD54",
  /** Muted red for failed checks on light surfaces. */
  failed: "#B42318",
  /** Failed-check red for the rare dark surface (camera overlay). */
  failedOnDark: "#FF8A80",
  white: "#FFFFFF",
} as const;

export const colors = {
  background: palette.offWhite,
  surface: palette.white,
  surfaceMuted: palette.seaMist,
  border: "#C9D6D0",
  text: palette.deepOcean,
  textMuted: "#4A5E5B",
  primary: palette.lagoonMint,
  onPrimary: palette.deepOcean,
  primaryInk: "#0D6B53",
  warning: palette.reviewAmber,
  onWarning: palette.deepOcean,
  warningInk: "#7A4A00",
  danger: palette.failed,
  onDanger: palette.white,
  dangerOnDark: palette.failedOnDark,
  chrome: palette.mangrove,
  onChrome: palette.offWhite,
  focus: palette.lagoonMint,
} as const;

export type StatusTone = "ok" | "review" | "failed" | "neutral";

/** Status fill + text colour pairs: mint = confirmed, amber = needs review, red = failed. */
export const tones: Record<StatusTone, { fill: string; text: string; ink: string }> = {
  ok: { fill: palette.lagoonMint, text: palette.deepOcean, ink: colors.primaryInk },
  review: { fill: palette.reviewAmber, text: palette.deepOcean, ink: colors.warningInk },
  failed: { fill: palette.failed, text: palette.white, ink: palette.failed },
  neutral: { fill: palette.seaMist, text: palette.deepOcean, ink: colors.textMuted },
};

export const space = { xs: 4, sm: 8, md: 16, lg: 24, xl: 32 } as const;
export const radius = { sm: 8, md: 12, lg: 16 } as const;

/** Minimum touch target (dp). Larger than the 48 dp guideline: gloves, wet hands, moving boats. */
export const touchTarget = 56;
