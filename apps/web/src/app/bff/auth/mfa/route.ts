import { ApiError } from "@aquacreds/api-client";
import { type NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { csrfProblem, problem, requireApiOrigin, serverClient } from "@/lib/bff";
import { COOKIES, setSession } from "@/lib/session";

const Body = z.object({ code: z.string().trim().min(6).max(32) });

/** Second step: TOTP or recovery code for the challenge stored by /bff/auth/login. */
export async function POST(req: NextRequest) {
  const csrf = csrfProblem(req);
  if (csrf) return csrf;
  const origin = requireApiOrigin();
  if (typeof origin !== "string") return origin;
  const mfaToken = req.cookies.get(COOKIES.mfa)?.value;
  if (!mfaToken) return problem(401, "The sign-in challenge expired. Enter your password again.");
  const parsed = Body.safeParse(await req.json().catch(() => null));
  if (!parsed.success) return problem(422, "Enter the 6-digit code or a recovery code");

  try {
    const pair = await serverClient(req, origin).auth.verifyMfa(mfaToken, parsed.data.code);
    const res = NextResponse.json({
      ok: true,
      recovery_codes_remaining: pair.recovery_codes_remaining ?? null,
    });
    res.cookies.delete(COOKIES.mfa);
    setSession(res, pair);
    return res;
  } catch (err) {
    if (err instanceof ApiError)
      return NextResponse.json(err.body ?? { detail: err.message }, { status: err.status });
    return problem(502, "The AquaCreds API is unreachable");
  }
}
