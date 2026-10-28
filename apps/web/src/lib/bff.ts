import "server-only";
import { createAquaClient, type TokenPair, unwrap } from "@aquacreds/api-client";
import { type NextRequest, NextResponse } from "next/server";
import { apiOrigin } from "./config";
import { CSRF_HEADER } from "./csrf";

export { CSRF_HEADER };

/**
 * CSRF guard for cookie-authenticated, state-changing requests: SameSite=Lax cookies + a custom
 * header + an Origin check (when the browser sends one).
 */
export function csrfProblem(req: NextRequest): NextResponse | null {
  if (req.method === "GET" || req.method === "HEAD") return null;
  if (req.headers.get(CSRF_HEADER) !== "1") return problem(403, "Missing CSRF header");
  const origin = req.headers.get("origin");
  if (origin && origin !== req.nextUrl.origin) return problem(403, "Cross-origin request refused");
  return null;
}

export function problem(status: number, detail: string): NextResponse {
  return NextResponse.json({ detail }, { status });
}

export function requireApiOrigin(): string | NextResponse {
  return apiOrigin() ?? problem(503, "AQUA_API_URL is not configured on the web server");
}

/**
 * fetch that forwards the browser's address so API rate limits apply per user, not per web
 * server (the API trusts it only when the web container is in TRUSTED_PROXY_IPS).
 */
export function forwardingFetch(req: NextRequest): typeof fetch {
  const incoming = req.headers.get("x-forwarded-for");
  const realIp = req.headers.get("x-real-ip");
  const chain = [incoming, !incoming ? realIp : null].filter(Boolean).join(", ");
  return (input, init) => {
    const request = new Request(input, init);
    if (chain) request.headers.set("x-forwarded-for", chain);
    request.headers.set("user-agent", req.headers.get("user-agent") ?? "aquacreds-web");
    return fetch(request, { cache: "no-store", signal: AbortSignal.timeout(20_000) });
  };
}

/** A typed API client for one BFF request (anonymous: tokens are handled by the BFF). */
export function serverClient(req: NextRequest, origin: string) {
  return createAquaClient({ baseUrl: origin, fetch: forwardingFetch(req) });
}

// ------------------------------------------------------------------ single-flight refresh
// Refresh tokens rotate and a replayed one revokes the whole family, so parallel requests that
// all see an expired access token must share ONE refresh. A rotated result is remembered briefly
// for late arrivals that still carry the old refresh cookie. (Per process: run one web replica,
// or move this to Valkey before scaling out.)
const inflight = new Map<string, Promise<TokenPair | null>>();
const recent = new Map<string, { pair: TokenPair; until: number }>();

export async function refreshOnce(
  req: NextRequest,
  origin: string,
  refreshToken: string,
): Promise<TokenPair | null> {
  const now = Date.now();
  for (const [k, v] of recent) if (v.until < now) recent.delete(k);
  const cached = recent.get(refreshToken);
  if (cached) return cached.pair;

  let p = inflight.get(refreshToken);
  if (!p) {
    p = (async () => {
      const client = serverClient(req, origin);
      try {
        const pair = await unwrap(
          client.api.POST("/api/v1/auth/refresh", { body: { refresh_token: refreshToken } }),
        );
        recent.set(refreshToken, { pair, until: Date.now() + 30_000 });
        return pair;
      } catch (err) {
        // 4xx = the session is over. 5xx / network errors are not proof of that: rethrow.
        if (err instanceof Error && "status" in err && Number(err.status) < 500) return null;
        throw err;
      }
    })().finally(() => inflight.delete(refreshToken));
    inflight.set(refreshToken, p);
  }
  return p;
}
