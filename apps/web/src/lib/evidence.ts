import type { Schema } from "@aquacreds/api-client";
import type { Status } from "@/components/ui";

export type Outcome = "pass" | "warn" | "fail" | "skipped";

export interface CheckResult {
  name: string;
  outcome: Outcome;
  reason: string;
  hard: boolean;
  details: Record<string, unknown> | null;
}

const OUTCOMES: readonly string[] = ["pass", "warn", "fail", "skipped"];

/** The API returns checks as loose JSON; keep only well-formed entries. */
export function parseChecks(raw: Schema<"SubmissionOut">["checks"] | undefined): CheckResult[] {
  if (!raw) return [];
  const out: CheckResult[] = [];
  for (const c of raw) {
    const { name, outcome, reason, hard, details } = c as Record<string, unknown>;
    if (typeof name !== "string" || typeof outcome !== "string" || !OUTCOMES.includes(outcome)) {
      continue;
    }
    out.push({
      name,
      outcome: outcome as Outcome,
      reason: typeof reason === "string" ? reason : "",
      hard: hard === true,
      details: details && typeof details === "object" ? (details as Record<string, unknown>) : null,
    });
  }
  return out;
}

export const OUTCOME_STATUS: Record<Outcome, Status> = {
  pass: "ok",
  warn: "review",
  fail: "failed",
  skipped: "neutral",
};

export const OUTCOME_LABEL: Record<Outcome, string> = {
  pass: "Pass",
  warn: "Warning",
  fail: "Fail",
  skipped: "Not run",
};

export const CHECK_INFO: Record<string, { label: string; explain: string }> = {
  exif_gps: {
    label: "EXIF location",
    explain:
      "The GPS position stored inside the photo file should match the position the app recorded. A mismatch can mean the file was edited or taken elsewhere.",
  },
  exif_time: {
    label: "EXIF capture time",
    explain:
      "The time stored inside the photo should match the time the app reports. Missing or far-off times are suspicious but not proof of a problem.",
  },
  capture_nonce: {
    label: "Live-capture window",
    explain:
      "Each photo must be taken inside a short window opened by a one-time code issued to that user for this period. This shows the photo was taken on site now, not reused.",
  },
  geofence: {
    label: "Inside project boundary",
    explain:
      "The photo position must fall inside the registered project boundary, allowing for the phone's reported GPS accuracy.",
  },
  duplicate_exact: {
    label: "Identical file",
    explain: "No byte-identical photo (same SHA-256) should already exist on the platform.",
  },
  duplicate_visual: {
    label: "Visually similar photo",
    explain:
      "A perceptual hash looks for photos that are nearly the same picture, even if re-saved or resized.",
  },
  photo_model: {
    label: "Photo plausibility (AI)",
    explain:
      "An image model estimates whether the photo plausibly shows mangroves. It is advisory only and never rejects a photo on its own.",
  },
  satellite_consistency: {
    label: "Satellite consistency",
    explain: "Comparison with satellite observations. Not available yet.",
  },
};

export function checkInfo(name: string): { label: string; explain: string } {
  return (
    CHECK_INFO[name] ?? {
      label: name.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()),
      explain: "An automated evidence check.",
    }
  );
}

/** Worst outcome among the checks that ran; null when none ran. */
export function worstOutcome(checks: CheckResult[]): Exclude<Outcome, "skipped"> | null {
  const ran = checks.filter((c) => c.outcome !== "skipped");
  if (ran.length === 0) return null;
  if (ran.some((c) => c.outcome === "fail")) return "fail";
  if (ran.some((c) => c.outcome === "warn")) return "warn";
  return "pass";
}

export function trustStatus(score: number | null): Status {
  if (score === null) return "neutral";
  if (score >= 80) return "ok";
  if (score >= 60) return "review";
  return "failed";
}

export interface SubmissionFilter {
  status: "all" | Schema<"SubmissionStatus">;
  outcome: "all" | "fail" | "warn" | "pass";
  kind: "all" | Schema<"SubmissionKind">;
}

export const NO_FILTER: SubmissionFilter = { status: "all", outcome: "all", kind: "all" };

export function filterSubmissions<T extends Schema<"SubmissionOut">>(
  subs: T[],
  f: SubmissionFilter,
): T[] {
  return subs.filter((s) => {
    if (f.status !== "all" && s.status !== f.status) return false;
    if (f.kind !== "all" && s.kind !== f.kind) return false;
    if (f.outcome !== "all" && worstOutcome(parseChecks(s.checks)) !== f.outcome) return false;
    return true;
  });
}
