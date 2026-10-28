import { type NextRequest, NextResponse } from "next/server";
import { csrfProblem, forwardingFetch, problem, requireApiOrigin } from "@/lib/bff";
import { featureFlags } from "@/lib/config";
import { DemoSession } from "@/lib/pending-contracts";
import { setDemoSession } from "@/lib/session";

/** Explore Demo: a short-lived, read-mostly session on the seeded demo data. No account needed. */
export async function POST(req: NextRequest) {
  const csrf = csrfProblem(req);
  if (csrf) return csrf;
  if (!featureFlags().demo) return problem(404, "The demo is not enabled on this deployment");
  const origin = requireApiOrigin();
  if (typeof origin !== "string") return origin;

  let res: Response;
  try {
    res = await forwardingFetch(req)(`${origin}/api/v1/demo/session`, { method: "POST" });
  } catch {
    return problem(502, "The AquaCreds API is unreachable");
  }
  const body = await res.json().catch(() => null);
  if (!res.ok)
    return NextResponse.json(body ?? { detail: "Demo session failed" }, { status: res.status });
  const parsed = DemoSession.safeParse(body);
  if (!parsed.success) return problem(502, "Unexpected demo session response from the API");

  const out = NextResponse.json({
    ok: true,
    project_ids: parsed.data.project_ids,
    banner: parsed.data.banner,
  });
  setDemoSession(out, parsed.data.access_token, parsed.data.expires_in, parsed.data.banner);
  return out;
}
