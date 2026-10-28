import "server-only";
import { apiOrigin, featureFlags } from "./config";
import { DemoStatus } from "./pending-contracts";

export type DemoAvailability =
  | { state: "available"; banner: string | null }
  | { state: "disabled"; reason: string };

/** Whether Explore Demo can start a session here: the flag, then the API's own switch. */
export async function demoAvailability(): Promise<DemoAvailability> {
  if (!featureFlags().demo) {
    return {
      state: "disabled",
      reason: "The demo is not enabled on this deployment (FEATURE_DEMO is off).",
    };
  }
  const origin = apiOrigin();
  if (!origin)
    return { state: "disabled", reason: "The web server has no API configured (AQUA_API_URL)." };
  try {
    const res = await fetch(`${origin}/api/v1/demo/status`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    if (!res.ok)
      return { state: "disabled", reason: `The API's demo status returned HTTP ${res.status}.` };
    const parsed = DemoStatus.safeParse(await res.json());
    if (!parsed.success)
      return { state: "disabled", reason: "The API returned an unexpected demo status." };
    return parsed.data.enabled
      ? { state: "available", banner: parsed.data.banner ?? null }
      : { state: "disabled", reason: "The API has the demo switched off." };
  } catch {
    return { state: "disabled", reason: "The AquaCreds API is unreachable right now." };
  }
}
