"use client";

import type { Schema } from "@aquacreds/api-client";
import { StatusPill } from "@/components/ui";
import {
  type CheckResult,
  checkInfo,
  OUTCOME_LABEL,
  OUTCOME_STATUS,
  parseChecks,
  trustStatus,
} from "@/lib/evidence";
import { formatDate, humanize, shortHash } from "@/lib/format";

type Submission = Schema<"SubmissionOut">;

const STATUS_LABEL: Record<Submission["status"], { s: "ok" | "review" | "failed"; l: string }> = {
  pending_checks: { s: "review", l: "Checks running" },
  checked: { s: "ok", l: "Checked" },
  rejected: { s: "failed", l: "Auto-rejected" },
};

const TRUST_LABEL = { ok: "Strong", review: "Look closer", failed: "Weak", neutral: "Unscored" };

export function CheckRow({ check }: { check: CheckResult }) {
  const info = checkInfo(check.name);
  return (
    <li className="py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{info.label}</span>
        <StatusPill status={OUTCOME_STATUS[check.outcome]}>
          {OUTCOME_LABEL[check.outcome]}
        </StatusPill>
        {check.hard && check.outcome === "fail" && (
          <span className="text-xs text-fail">hard failure</span>
        )}
      </div>
      <p className="mt-0.5 text-sm">{check.reason}</p>
      <p className="mt-0.5 text-xs text-fg-muted">{info.explain}</p>
    </li>
  );
}

/** One field submission: evidence reference, trust score, and every check with its explanation. */
export function SubmissionCard({ sub, plotCode }: { sub: Submission; plotCode: string | null }) {
  const checks = parseChecks(sub.checks);
  const st = STATUS_LABEL[sub.status];
  const score = sub.trust_score;
  const band = trustStatus(score);
  return (
    <article
      className="rounded-[var(--radius-card)] border border-line bg-panel p-4"
      aria-label={`Submission ${shortHash(sub.id, 8, 0)}`}
    >
      <div className="flex gap-4">
        {/* The API gives no presigned GET for photos yet, so show the evidence hash instead. */}
        <div
          role="img"
          aria-label={`Photo not previewable; SHA-256 ${sub.sha256}`}
          className="tabular flex size-20 shrink-0 flex-col items-center justify-center rounded-lg border border-dashed border-line bg-panel-raised px-1 text-center font-mono text-[10px] leading-tight text-fg-muted"
        >
          <span>SHA-256</span>
          <span>{sub.sha256.slice(0, 8)}</span>
          <span>{sub.sha256.slice(8, 16)}</span>
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold">
              {humanize(sub.kind)}
              {plotCode && ` · plot ${plotCode}`}
            </h3>
            <StatusPill status={st.s}>{st.l}</StatusPill>
          </div>
          <p className="tabular mt-1 text-xs text-fg-muted">
            Captured {formatDate(sub.captured_at)} · {sub.lat.toFixed(5)}, {sub.lon.toFixed(5)} ·
            GPS ±{sub.gps_accuracy_m.toFixed(0)} m
          </p>
          <p className="mt-2 flex flex-wrap items-baseline gap-2">
            <span className="text-xs uppercase tracking-wide text-fg-muted">Trust score</span>
            {score === null ? (
              <span className="text-sm text-fg-muted">not scored yet</span>
            ) : (
              <>
                <span className="tabular font-display text-2xl font-semibold">{score}</span>
                <span className="text-xs text-fg-muted">/ 100</span>
                <StatusPill status={band}>{TRUST_LABEL[band]}</StatusPill>
              </>
            )}
          </p>
        </div>
      </div>
      {checks.length === 0 ? (
        <p className="mt-3 text-sm text-fg-muted">No check results yet.</p>
      ) : (
        <ul className="mt-3 divide-y divide-line border-t border-line">
          {checks.map((c) => (
            <CheckRow key={c.name} check={c} />
          ))}
        </ul>
      )}
    </article>
  );
}
