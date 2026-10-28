import { type NextRequest, NextResponse } from "next/server";
import { csrfProblem, forwardingFetch, problem, refreshOnce, requireApiOrigin } from "@/lib/bff";
import { proxyable } from "@/lib/proxy-policy";
import { COOKIES, clearSession, type SessionTokens, setSession } from "@/lib/session";

type Ctx = { params: Promise<{ path: string[] }> };

const MAX_BODY_BYTES = 2 * 1024 * 1024; // JSON only; files go straight to storage (presigned PUT)
const PASS_REQUEST = ["content-type", "accept", "if-none-match"];
const PASS_RESPONSE = [
  "content-type",
  "content-disposition",
  "cache-control",
  "etag",
  "retry-after",
];

/** Backend-for-frontend proxy: adds the bearer token from the httpOnly cookie, refreshes once on
 *  401 (single-flight), and never exposes tokens to the browser. */
async function handle(req: NextRequest, ctx: Ctx): Promise<NextResponse> {
  const csrf = csrfProblem(req);
  if (csrf) return csrf;
  const { path } = await ctx.params;
  if (!proxyable(req.method, path)) return problem(404, "Not available through the web proxy");
  const origin = requireApiOrigin();
  if (typeof origin !== "string") return origin;

  let body: ArrayBuffer | undefined;
  if (req.method !== "GET" && req.method !== "HEAD") {
    body = await req.arrayBuffer();
    if (body.byteLength > MAX_BODY_BYTES) return problem(413, "Request body too large");
  }
  const target = `${origin}/api/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const doFetch = forwardingFetch(req);
  const send = (token: string | undefined) => {
    const headers = new Headers();
    for (const h of PASS_REQUEST) {
      const v = req.headers.get(h);
      if (v) headers.set(h, v);
    }
    if (token) headers.set("authorization", `Bearer ${token}`);
    return doFetch(target, { method: req.method, headers, ...(body ? { body } : {}) });
  };

  let access = req.cookies.get(COOKIES.access)?.value;
  const refresh = req.cookies.get(COOKIES.refresh)?.value;
  let rotated: SessionTokens | null = null;
  let ended = false;

  try {
    if (!access && refresh) {
      const pair = await refreshOnce(req, origin, refresh);
      if (pair) {
        rotated = pair;
        access = pair.access_token;
      } else ended = true;
    }
    let upstream = await send(access);
    if (upstream.status === 401 && refresh && !rotated && !ended) {
      const pair = await refreshOnce(req, origin, refresh);
      if (pair) {
        rotated = pair;
        upstream = await send(pair.access_token);
      } else ended = true;
    }

    const headers = new Headers();
    for (const h of PASS_RESPONSE) {
      const v = upstream.headers.get(h);
      if (v) headers.set(h, v);
    }
    if (!headers.has("cache-control")) headers.set("cache-control", "no-store");
    const res = new NextResponse(upstream.body, { status: upstream.status, headers });
    if (rotated) setSession(res, rotated);
    if (ended || (upstream.status === 401 && !req.cookies.get(COOKIES.demo))) {
      res.headers.set("x-aquacreds-session", "ended");
      if (ended) clearSession(res);
    }
    return res;
  } catch {
    return problem(502, "The AquaCreds API is unreachable");
  }
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
