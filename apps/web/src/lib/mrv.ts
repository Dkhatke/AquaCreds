import type { Schema } from "@aquacreds/api-client";
import type { Status } from "@/components/ui";

type ReportStatus = Schema<"ReportStatus">;
type PeriodStatus = Schema<"PeriodStatus">;

/** Shown beside every carbon figure (DESIGN_DIRECTION §2). */
export const ESTIMATE_LABEL = "Estimated — pending accredited verification; testnet registry units";

export const REPORT_STATUS: Record<ReportStatus, { status: Status; label: string }> = {
  draft: { status: "neutral", label: "Draft" },
  under_review: { status: "review", label: "Under review" },
  changes_requested: { status: "review", label: "Changes requested" },
  approved: { status: "ok", label: "Approved" },
  rejected: { status: "failed", label: "Rejected" },
};

export const PERIOD_STATUS: Record<PeriodStatus, { status: Status; label: string }> = {
  open: { status: "neutral", label: "Open" },
  analysing: { status: "review", label: "Building report" },
  under_review: { status: "review", label: "Under review" },
  changes_requested: { status: "review", label: "Changes requested" },
  approved: { status: "ok", label: "Approved" },
  rejected: { status: "failed", label: "Rejected" },
};

/** Periods in which an organisation admin may request a (new) report. */
export const REPORTABLE_PERIODS: ReadonlySet<PeriodStatus> = new Set(["open", "changes_requested"]);

const EXPLORERS: Record<number, string> = { 80002: "https://amoy.polygonscan.com" };

/** Block-explorer link for a transaction; only chains we know (Polygon Amoy testnet). */
export function explorerTxUrl(chainId: number, txHash: string): string | null {
  const base = EXPLORERS[chainId];
  return base ? `${base}/tx/${txHash}` : null;
}

export function chainName(chainId: number): string {
  return chainId === 80002 ? "Polygon Amoy testnet" : `chain ${chainId}`;
}

export type ReviewDecision = Schema<"ReviewRequest">["decision"];

export const DECISIONS: { id: ReviewDecision; label: string }[] = [
  { id: "approve", label: "Approve" },
  { id: "request_changes", label: "Request changes" },
  { id: "reject", label: "Reject" },
];

/** The API requires a comment unless approving; mirror it so the form explains instead of 422-ing. */
export function reviewError(decision: ReviewDecision, comment: string): string | null {
  if (decision !== "approve" && comment.trim() === "") {
    return "A comment is required when requesting changes or rejecting.";
  }
  if (comment.length > 5000) return "Comments are limited to 5000 characters.";
  return null;
}

export interface StratumInput {
  stratum: string;
  included: boolean;
  /** Raw text of the area field. */
  area: string;
}

export interface StrataResult {
  total: number;
  errors: string[];
  /** Present only when valid: exactly the included strata, in hectares. */
  payload: Record<string, number> | null;
}

/** Strata areas must be positive, cover exactly the measured strata, and sum to <= project area. */
export function validateStrata(rows: StratumInput[], projectAreaHa: number): StrataResult {
  const chosen = rows.filter((r) => r.included);
  const errors: string[] = [];
  if (chosen.length === 0) errors.push("Select at least one measured stratum.");
  const payload: Record<string, number> = {};
  let total = 0;
  for (const r of chosen) {
    const v = Number(r.area);
    if (r.area.trim() === "" || !Number.isFinite(v) || v <= 0) {
      errors.push(`Enter a positive area for stratum "${r.stratum}".`);
      continue;
    }
    payload[r.stratum] = v;
    total += v;
  }
  if (total > projectAreaHa * (1 + 1e-9)) {
    errors.push(
      `Stratum areas (${total.toFixed(2)} ha) exceed the project area (${projectAreaHa.toFixed(2)} ha).`,
    );
  }
  return { total, errors, payload: errors.length === 0 ? payload : null };
}

/** One stratum defaults to the whole project area (as the API does); several start empty. */
export function defaultStrata(strata: string[], projectAreaHa: number): StratumInput[] {
  const unique = [...new Set(strata)].sort();
  return unique.map((stratum) => ({
    stratum,
    included: true,
    area: unique.length === 1 ? String(projectAreaHa) : "",
  }));
}

export type HashVerdict = { status: Status; label: string };

/** Plain-language outcome of GET /reports/{id}/verify. */
export function verificationSummary(v: Schema<"ReportVerification">): {
  stored: HashVerdict;
  onchain: HashVerdict;
} {
  const stored: HashVerdict =
    v.stored_hash === null
      ? { status: "failed", label: "Stored record missing" }
      : v.stored_matches
        ? { status: "ok", label: "Stored record matches" }
        : { status: "failed", label: "Stored record does NOT match" };
  const onchain: HashVerdict =
    v.onchain_matches === null
      ? { status: "neutral", label: "Not anchored on-chain yet" }
      : v.onchain_matches
        ? { status: "ok", label: "On-chain hash matches" }
        : { status: "failed", label: "On-chain hash does NOT match" };
  return { stored, onchain };
}

/** A conflict-of-interest 403 is shown verbatim from the API; others get a generic fallback. */
export function isConflictOfInterest(message: string): boolean {
  return /conflict of interest/i.test(message);
}
