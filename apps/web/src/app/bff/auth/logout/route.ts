import { type NextRequest, NextResponse } from "next/server";
import { csrfProblem, requireApiOrigin, serverClient } from "@/lib/bff";
import { COOKIES, clearSession } from "@/lib/session";

/** Revokes the refresh-token family server-side (best effort), then clears the cookies. */
export async function POST(req: NextRequest) {
  const csrf = csrfProblem(req);
  if (csrf) return csrf;
  const origin = requireApiOrigin();
  const refresh = req.cookies.get(COOKIES.refresh)?.value;
  if (typeof origin === "string" && refresh) {
    await serverClient(req, origin)
      .api.POST("/api/v1/auth/logout", { body: { refresh_token: refresh } })
      .catch(() => undefined);
  }
  const res = NextResponse.json({ ok: true });
  clearSession(res);
  return res;
}
