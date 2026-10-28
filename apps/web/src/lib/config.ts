import "server-only";

/**
 * Server-side configuration, read at request time (so one container image works everywhere).
 *
 * - `AQUA_API_URL`: API origin as seen from the Next.js server (e.g. `http://api:8000` inside
 *   compose). Falls back to `NEXT_PUBLIC_API_URL` (e.g. `https://api.aquacreds.in`).
 * - `FEATURE_DEMO=1`: show Explore Demo (needs `POST /api/v1/demo/session` on the API).
 * - `FEATURE_TILES=1`: request classification tiles (`GET /projects/{id}/tilejson`).
 */
export function apiOrigin(): string | null {
  const raw = process.env.AQUA_API_URL || process.env.NEXT_PUBLIC_API_URL || "";
  return raw ? raw.replace(/\/+$/, "") : null;
}

export interface FeatureFlags {
  demo: boolean;
  tiles: boolean;
}

const on = (value: string | undefined) => value === "1" || value === "true";

export function featureFlags(): FeatureFlags {
  return { demo: on(process.env.FEATURE_DEMO), tiles: on(process.env.FEATURE_TILES) };
}

/** Secure cookies everywhere except plain-http local development. */
export function secureCookies(): boolean {
  return process.env.NODE_ENV === "production" && process.env.COOKIE_INSECURE !== "1";
}
