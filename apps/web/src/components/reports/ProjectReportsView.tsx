"use client";

import type { Schema } from "@aquacreds/api-client";
import Link from "next/link";
import { useState } from "react";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  ProvenanceNote,
  StatusPill,
} from "@/components/ui";
import { formatDate, formatNumber, humanize, shortHash } from "@/lib/format";
import { ESTIMATE_LABEL, PERIOD_STATUS, REPORT_STATUS, REPORTABLE_PERIODS } from "@/lib/mrv";
import { useMe, usePeriods, usePlots, useProject, useReportsForPeriods } from "@/lib/queries";
import { BuildReportForm } from "./BuildReportForm";

type Report = Schema<"ReportOut">;
type Period = Schema<"PeriodOut">;

export function ReportVersionRow({ report }: { report: Report }) {
  const st = REPORT_STATUS[report.status];
  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line/60 py-2.5 first:border-t-0">
      <Link
        href={`/console/reports/${report.id}`}
        className="font-medium underline-offset-2 hover:underline"
      >
        Version {report.version}
      </Link>
      <StatusPill status={st.status}>{st.label}</StatusPill>
      <span className="tabular text-sm text-fg-muted">
        lCCC {formatNumber(report.lccc_units)} · tCCC {formatNumber(report.tccc_units)} units
      </span>
      <span className="tabular text-xs text-fg-muted" title={report.report_hash}>
        {shortHash(report.report_hash, 8, 6)}
      </span>
      <span className="text-xs text-fg-muted">Generated {formatDate(report.generated_at)}</span>
    </li>
  );
}

function PeriodCard({
  period,
  reports,
  canBuild,
  buildBlocked,
  onBuild,
  building,
}: {
  period: Period;
  reports: { data?: Report[] | undefined; isPending: boolean; isError: boolean; error: unknown };
  canBuild: boolean;
  buildBlocked: string | null;
  onBuild: () => void;
  building: boolean;
}) {
  const ps = PERIOD_STATUS[period.status];
  return (
    <Card
      as="article"
      title={
        <>
          {humanize(period.kind)} period · {formatDate(period.start_date)} to{" "}
          {formatDate(period.end_date)}
        </>
      }
      actions={<StatusPill status={ps.status}>{ps.label}</StatusPill>}
    >
      {period.status === "analysing" && (
        <p role="status" className="mb-3 text-sm text-warn">
          The carbon engine is building a report. This page refreshes on its own.
        </p>
      )}
      {reports.isPending ? (
        <LoadingState label="Loading report versions" />
      ) : reports.isError ? (
        <ErrorState error={reports.error} />
      ) : reports.data && reports.data.length > 0 ? (
        <ol aria-label="Report versions, newest first">
          {reports.data.map((r) => (
            <ReportVersionRow key={r.id} report={r} />
          ))}
        </ol>
      ) : (
        <p className="text-sm text-fg-muted">No report has been built for this period yet.</p>
      )}
      {canBuild && !building && (
        <div className="mt-3">
          {buildBlocked ? (
            <p className="text-xs text-fg-muted">{buildBlocked}</p>
          ) : (
            <Button variant="secondary" size="sm" onClick={onBuild}>
              Build report
            </Button>
          )}
        </div>
      )}
    </Card>
  );
}

export function ProjectReportsView({ projectId, demo }: { projectId: string; demo: boolean }) {
  const project = useProject(projectId);
  const periods = usePeriods(projectId);
  const plots = usePlots(projectId);
  const me = useMe();
  const sorted = [...(periods.data ?? [])].sort((a, b) => b.start_date.localeCompare(a.start_date));
  const reportQs = useReportsForPeriods(sorted.map((p) => p.id));
  const [buildFor, setBuildFor] = useState<string | null>(null);

  if (project.isPending) return <LoadingState className="p-6" label="Loading project" />;
  if (project.isError) {
    return (
      <div className="p-6">
        <ErrorState
          title="This project could not be loaded"
          error={project.error}
          onRetry={() => void project.refetch()}
        />
      </div>
    );
  }
  const p = project.data;
  const isAdmin =
    !demo &&
    (me.data?.memberships.some((m) => m.org_id === p.org_id && m.role === "admin") ?? false);
  const strata = [...new Set((plots.data ?? []).map((pl) => pl.stratum))];

  return (
    <div className="surface-light min-h-full bg-bg p-4 text-fg md:p-8">
      <div className="mx-auto max-w-4xl">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-ok">MRV reports</p>
        <h1 className="mt-2 font-display text-3xl font-semibold">{p.name}</h1>
        <p className="mt-2 max-w-2xl text-fg-muted">
          Each monitoring period produces hashed, versioned reports computed by the carbon engine.
          An independent verifier decides each version.
        </p>
        <p
          role="note"
          className="mt-4 rounded-lg border border-review-amber/70 bg-review-amber/14 px-3 py-2 text-sm text-warn"
        >
          {ESTIMATE_LABEL}.
        </p>

        <div className="mt-6">
          {periods.isPending ? (
            <LoadingState label="Loading monitoring periods" />
          ) : periods.isError ? (
            <ErrorState error={periods.error} onRetry={() => void periods.refetch()} />
          ) : sorted.length === 0 ? (
            <EmptyState title="No monitoring periods yet">
              Periods are created by the project's organisation before field evidence and reports
              can be recorded.
            </EmptyState>
          ) : (
            <ul className="space-y-4">
              {sorted.map((period, i) => {
                const q = reportQs[i];
                const canBuild = isAdmin && REPORTABLE_PERIODS.has(period.status);
                const blocked = plots.isPending
                  ? "Loading plots…"
                  : plots.isError
                    ? "Plots could not be loaded, so the stratum list is unavailable."
                    : null;
                return (
                  <li key={period.id}>
                    <PeriodCard
                      period={period}
                      reports={{
                        data: q?.data,
                        isPending: q?.isPending ?? true,
                        isError: q?.isError ?? false,
                        error: q?.error,
                      }}
                      canBuild={canBuild}
                      buildBlocked={blocked}
                      onBuild={() => setBuildFor(period.id)}
                      building={buildFor === period.id}
                    />
                    {buildFor === period.id && (
                      <BuildReportForm
                        project={p}
                        periodId={period.id}
                        strata={strata}
                        onClose={() => setBuildFor(null)}
                      />
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
        <ProvenanceNote
          className="mt-6"
          source="GET /api/v1/projects/{id}/periods and /periods/{id}/reports"
        />
        {demo && (
          <p className="mt-2 text-xs text-fg-muted">
            Demo data is read-only: building and reviewing reports is disabled.
          </p>
        )}
      </div>
    </div>
  );
}
