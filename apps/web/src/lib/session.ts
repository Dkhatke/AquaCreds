import "server-only";
import type { NextResponse } from "next/server";
import { secureCookies } from "./config";

/**
 * Browser session = httpOnly cookies set by the Next.js server (backend-for-frontend). Tokens never
 * reach client-side JavaScript or localStorage; see apps/web/README.md ("Auth").
 */
export const COOKIES = {
  access: "aq_at",
  refresh: "aq_rt",
  mfa: "aq_mfa",
  demo: "aq_demo",
} as const;

/** API default refresh TTL (`JWT_REFRESH_TTL_DAYS`); the API remains the authority. */
const REFRESH_MAX_AGE_S = 30 * 24 * 3600;

function base() {
  return { httpOnly: true, secure: secureCookies(), sameSite: "lax" as const, path: "/" };
}

export interface SessionTokens {
  access_token: string;
  expires_in: number;
  refresh_token?: string | null | undefined;
}

export function setSession(res: NextResponse, tokens: SessionTokens): void {
  res.cookies.set(COOKIES.access, tokens.access_token, {
    ...base(),
    // Expire a little early so the BFF refreshes before the API starts returning 401s.
    maxAge: Math.max(tokens.expires_in - 30, 30),
  });
  if (tokens.refresh_token) {
    res.cookies.set(COOKIES.refresh, tokens.refresh_token, {
      ...base(),
      maxAge: REFRESH_MAX_AGE_S,
    });
  }
}

/** Demo sessions have no refresh token; the banner text is kept for the console layout. */
export function setDemoSession(
  res: NextResponse,
  accessToken: string,
  expiresIn: number,
  banner: string,
) {
  clearSession(res);
  setSession(res, { access_token: accessToken, expires_in: expiresIn });
  res.cookies.set(COOKIES.demo, banner.slice(0, 600) || "1", { ...base(), maxAge: expiresIn });
}

export function setMfaChallenge(res: NextResponse, mfaToken: string, expiresIn: number) {
  res.cookies.set(COOKIES.mfa, mfaToken, { ...base(), maxAge: expiresIn });
}

export function clearSession(res: NextResponse): void {
  for (const name of Object.values(COOKIES)) res.cookies.set(name, "", { ...base(), maxAge: 0 });
}
