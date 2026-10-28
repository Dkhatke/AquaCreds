import { type NextRequest, NextResponse } from "next/server";
import { COOKIES } from "@/lib/session";

/** What the browser may know about its session: whether one exists, and whether it is a demo. */
export function GET(req: NextRequest) {
  const demo = req.cookies.get(COOKIES.demo)?.value;
  const authenticated = Boolean(
    req.cookies.get(COOKIES.access)?.value || req.cookies.get(COOKIES.refresh)?.value,
  );
  return NextResponse.json(
    { authenticated, demo: Boolean(demo), banner: demo && demo !== "1" ? demo : null },
    { headers: { "cache-control": "no-store" } },
  );
}
