import { ApiError } from "@aquacreds/api-client";
import { type NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { csrfProblem, problem, requireApiOrigin, serverClient } from "@/lib/bff";
import { clearSession, setMfaChallenge, setSession } from "@/lib/session";

const Body = z.object({ email: z.string().min(3).max(320), password: z.string().min(1).max(1024) });

/** Password step. Tokens go into httpOnly cookies; an MFA challenge token is kept the same way. */
export async function POST(req: NextRequest) {
  const csrf = csrfProblem(req);
  if (csrf) return csrf;
  const origin = requireApiOrigin();
  if (typeof origin !== "string") return origin;
  const parsed = Body.safeParse(await req.json().catch(() => null));
  if (!parsed.success) return problem(422, "Email and password are required");

  try {
    const result = await serverClient(req, origin).auth.login(
      parsed.data.email,
      parsed.data.password,
    );
    if ("mfa_required" in result) {
      const res = NextResponse.json({ mfa_required: true, expires_in: result.expires_in });
      clearSession(res);
      setMfaChallenge(res, result.mfa_token, result.expires_in);
      return res;
    }
    const res = NextResponse.json({ ok: true });
    clearSession(res);
    setSession(res, result);
    return res;
  } catch (err) {
    if (err instanceof ApiError)
      return NextResponse.json(err.body ?? { detail: err.message }, { status: err.status });
    return problem(502, "The AquaCreds API is unreachable");
  }
}
