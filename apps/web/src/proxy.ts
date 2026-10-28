import { type NextRequest, NextResponse } from "next/server";

// Duplicated from lib/session (Proxy should not import server modules with side effects).
const ACCESS = "aq_at";
const REFRESH = "aq_rt";

/** Console pages need a session cookie; the API still authorises every request. */
export function proxy(req: NextRequest) {
  const hasSession = req.cookies.has(ACCESS) || req.cookies.has(REFRESH);
  if (hasSession) return NextResponse.next();
  const login = new URL("/console/login", req.url);
  login.searchParams.set("next", req.nextUrl.pathname + req.nextUrl.search);
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/console", "/console/((?!login|demo).*)"],
};
