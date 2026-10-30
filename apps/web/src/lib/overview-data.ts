import type { AlertOut, ProjectOut, Schema, SubmissionOut } from "@aquacreds/api-client";

/** One API call's outcome: a failure is kept (and shown) per part, never replaced by a number. */
export type Part<T> = { ok: true; data: T } | { ok: false; error: string };

export async function settle<T>(p: Promise<T>): Promise<Part<T>> {
  try {
    return { ok: true, data: await p };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : "Request failed" };
  }
}

type Classification = Schema<"ClassificationOut">;
type Report = Schema<"ReportOut">;
type Period = Schema<"PeriodOut">;

export interface ProjectSummary {
  project: ProjectOut;
  classification: Part<Classification | null>;
  ndvi: Part<{ value: number; date: string } | null>;
  openAlerts: Part<AlertOut[]>;
  periods: Part<Period[]>;
  reports: Part<Report[]>;
  submissions: Part<{ period: Period; items: SubmissionOut[] }[]>;
}

/** Newest first. */
export function latestReport(reports: Report[]): Report | null {
  return [...reports].sort((a, b) => b.generated_at.localeCompare(a.generated_at))[0] ?? null;
}

export function openAlerts(alerts: AlertOut[]): AlertOut[] {
  return alerts.filter((a) => a.acknowledged_at === null);
}

export interface Attention {
  kind:
    | "pendingReview"
    | "changesRequested"
    | "projectChanges"
    | "failedSubmissions"
    | "lowCodes"
    | "mfaRequired"
    | "openAlerts";
  count: number;
  name?: string;
  href: string;
  severity: "review" | "failed";
}

export interface AttentionInput {
  summaries: ProjectSummary[];
  /** Platform role of the signed-in user. Only verifiers / registry admins get a review queue. */
  platformRole: Schema<"PlatformRole"> | null;
  mfa: Schema<"MfaStatusOut"> | null;
  /** Warn when this many unused recovery codes (or fewer) remain. */
  lowCodeThreshold?: number;
}

/** Everything here is computed from API responses; a part that failed to load adds nothing. */
export function buildAttention({
  summaries,
  platformRole,
  mfa,
  lowCodeThreshold = 3,
}: AttentionInput): Attention[] {
  const out: Attention[] = [];

  if (platformRole === "verifier" || platformRole === "registry_admin") {
    const pending = summaries.flatMap((s) =>
      s.reports.ok ? s.reports.data.filter((r) => r.status === "under_review") : [],
    );
    if (pending.length > 0) {
      out.push({
        kind: "pendingReview",
        count: pending.length,
        href: "/console/reports",
        severity: "review",
      });
    }
  }

  for (const s of summaries) {
    if (s.project.status === "changes_requested") {
      out.push({
        kind: "projectChanges",
        count: 1,
        name: s.project.name,
        href: "/console/registry",
        severity: "review",
      });
    }
    if (s.reports.ok) {
      const r = latestReport(s.reports.data);
      if (r?.status === "changes_requested") {
        out.push({
          kind: "changesRequested",
          count: 1,
          name: s.project.name,
          href: "/console/reports",
          severity: "review",
        });
      }
    }
    if (s.openAlerts.ok && s.openAlerts.data.length > 0) {
      out.push({
        kind: "openAlerts",
        count: s.openAlerts.data.length,
        name: s.project.name,
        href: `/console/projects/${s.project.id}/monitoring`,
        severity: s.openAlerts.data.some((a) => a.severity === "high") ? "failed" : "review",
      });
    }
  }

  const failed = summaries.reduce(
    (n, s) =>
      n +
      (s.submissions.ok
        ? s.submissions.data.reduce(
            (m, p) => m + p.items.filter((i) => i.status === "rejected").length,
            0,
          )
        : 0),
    0,
  );
  if (failed > 0) {
    out.push({
      kind: "failedSubmissions",
      count: failed,
      href: "/console/community",
      severity: "failed",
    });
  }

  if (mfa) {
    if (mfa.enabled && mfa.recovery_codes_remaining <= lowCodeThreshold) {
      out.push({
        kind: "lowCodes",
        count: mfa.recovery_codes_remaining,
        href: "/console/account",
        severity: mfa.recovery_codes_remaining === 0 ? "failed" : "review",
      });
    }
    if (mfa.required && !mfa.enabled) {
      out.push({ kind: "mfaRequired", count: 0, href: "/console/account", severity: "failed" });
    }
  }
  return out;
}

export interface TrustBand {
  from: number;
  to: number;
  count: number;
}

/** Counts of scored submissions in ten-point bands (0–9 … 90–100). Unscored ones are excluded. */
export function trustBands(items: SubmissionOut[]): TrustBand[] {
  const bands: TrustBand[] = Array.from({ length: 10 }, (_, i) => ({
    from: i * 10,
    to: i === 9 ? 100 : i * 10 + 9,
    count: 0,
  }));
  for (const s of items) {
    if (s.trust_score === null) continue;
    const i = Math.min(9, Math.max(0, Math.floor(s.trust_score / 10)));
    (bands[i] as TrustBand).count += 1;
  }
  return bands;
}

export interface PeriodActivity {
  projectName: string;
  period: Period;
  total: number;
  checked: number;
  rejected: number;
  pending: number;
}

export function periodActivity(summaries: ProjectSummary[]): PeriodActivity[] {
  return summaries.flatMap((s) =>
    s.submissions.ok
      ? s.submissions.data.map(({ period, items }) => ({
          projectName: s.project.name,
          period,
          total: items.length,
          checked: items.filter((i) => i.status === "checked").length,
          rejected: items.filter((i) => i.status === "rejected").length,
          pending: items.filter((i) => i.status === "pending_checks").length,
        }))
      : [],
  );
}

/** Poll delay: base while counts change, doubling (capped) while nothing changes or calls fail. */
export function pollDelay(steps: number, base = 30_000, cap = 300_000): number {
  return Math.min(cap, base * 2 ** Math.max(0, steps));
}
