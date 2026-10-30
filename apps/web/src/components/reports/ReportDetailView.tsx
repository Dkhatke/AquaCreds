"use client";

import type { Schema } from "@aquacreds/api-client";
import Link from "next/link";
import {
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  ProvenanceNote,
  Stat,
  StatusPill,
} from "@/components/ui";
import { formatDate, formatNumber, humanize } from "@/lib/format";
import { ESTIMATE_LABEL, REPORT_STATUS } from "@/lib/mrv";
import {
  useMe,
  usePeriods,
  useProject,
  usePublicReport,
  useReport,
  useReportsForPeriods,
} from "@/lib/queries";
import { AnchoringPanel } from "./AnchoringPanel";
import { ReviewPanel } from "./ReviewPanel";
import { VerifyPanel } from "./VerifyPanel";

type Report = Schema<"ReportDetail">;

function Downloads({ report }: { report: Report }) {
  const items = [
    { label: "PDF report", href: report.downloads.pdf_url },
    { label: "JSON record", href: report.downloads.json_url },
    { label: "CSV tables (zip)", href: report.downloads.csv_url },
  ];
  return (
    <Card
      title="Downloads"
      meta="Presigned links, valid for a few minutes: reload the page for fresh ones"
    >
      <ul className="flex flex-wrap gap-2">
        {items.map((i) => (
          <li key={i.label}>
            <a
              href={i.href}
              download
              className="inline-flex h-9 items-center rounded-full border border-current px-4 text-sm font-medium hover:bg-panel-raised"
            >
              {i.label}
            </a>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Traceability({ report }: { report: Report }) {
  const approved = report.status === "approved";
  const pub = usePublicReport(report.id, approved);
  return (
    <Card title="Methodology profile and software" meta="Read from the hash-checked stored record">
      {!approved ? (
        <EmptyState title="Available once the report is approved">
          The methodology profile and software versions are read from the stored record through the
          public report endpoint, which only serves approved reports. The JSON download above holds
          them for every version.
        </EmptyState>
      ) : pub.isPending ? (
        <LoadingState label="Loading methodology profile" />
      ) : pub.isError ? (
        <ErrorState
          title="Not available from the public registry"
          error={pub.error}
          onRetry={() => void pub.refetch()}
        />
      ) : (
        <div className="space-y-4">
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <Stat
              label="Tree stock"
              value={formatNumber(pub.data.tree_stock_tco2e, 2)}
              unit="tCO₂e"
              provenance={{ label: "Estimated" }}
            />
            <Stat
              label="tCCC"
              value={formatNumber(pub.data.tccc_tco2e, 2)}
              unit="tCO₂e"
              provenance={{ label: "Estimated" }}
            />
            <Stat label="Methodology" value={humanize(pub.data.project_methodology)} />
          </dl>
          <div>
            <h3 className="text-xs font-medium uppercase tracking-wide text-fg-muted">
              Methodology profile
            </h3>
            <dl className="mt-1 grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
              {Object.entries(pub.data.traceability.methodology_profile).map(([k, v]) => (
                <div key={k} className="flex justify-between gap-3 border-b border-line/50 py-1">
                  <dt className="text-fg-muted">{humanize(k)}</dt>
                  <dd className="tabular text-right">
                    {typeof v === "object" ? JSON.stringify(v) : String(v)}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
          <div>
            <h3 className="text-xs font-medium uppercase tracking-wide text-fg-muted">
              Software versions
            </h3>
            <dl className="mt-1 grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
              {Object.entries(pub.data.traceability.software).map(([k, v]) => (
                <div key={k} className="flex justify-between gap-3 border-b border-line/50 py-1">
                  <dt className="text-fg-muted">{k}</dt>
                  <dd className="tabular">{v}</dd>
                </div>
              ))}
            </dl>
          </div>
          <ProvenanceNote
            source={`record schema ${pub.data.traceability.record_schema}`}
            label={pub.data.traceability.status_label}
          />
        </div>
      )}
    </Card>
  );
}

function ReviewHistory({ periodId, currentId }: { periodId: string; currentId: string }) {
  const [q] = useReportsForPeriods([periodId]);
  return (
    <Card title="Review history" meta="Every version of this period's report and its decision">
      {!q || q.isPending ? (
        <LoadingState label="Loading review history" />
      ) : q.isError ? (
        <ErrorState error={q.error} />
      ) : (
        <ol className="space-y-3">
          {q.data.map((r) => {
            const st = REPORT_STATUS[r.status];
            return (
              <li key={r.id} className="border-l-2 border-line pl-3">
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  {r.id === currentId ? (
                    <span className="font-medium">Version {r.version} (this page)</span>
                  ) : (
                    <Link href={`/console/reports/${r.id}`} className="font-medium underline">
                      Version {r.version}
                    </Link>
                  )}
                  <StatusPill status={st.status}>{st.label}</StatusPill>
                </div>
                <p className="mt-0.5 text-xs text-fg-muted">
                  Generated {formatDate(r.generated_at)}
                  {r.decided_at && ` · decided ${formatDate(r.decided_at)}`}
                </p>
                {r.decision_comment && (
                  <blockquote className="mt-1 text-sm">“{r.decision_comment}”</blockquote>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </Card>
  );
}

export function ReportDetailView({ reportId, demo }: { reportId: string; demo: boolean }) {
  const report = useReport(reportId);
  const projectId = report.data?.project_id ?? "";
  const project = useProject(projectId);
  const me = useMe();
  const periods = usePeriods(projectId);

  if (report.isPending) return <LoadingState className="p-6" label="Loading report" />;
  if (report.isError) {
    return (
      <div className="surface-light bg-bg p-6">
        <ErrorState
          title="This report could not be loaded"
          error={report.error}
          onRetry={() => void report.refetch()}
        />
      </div>
    );
  }
  const r = report.data;
  const st = REPORT_STATUS[r.status];
  const period = periods.data?.find((p) => p.id === r.period_id);
  const isVerifier = me.data?.platform_role === "verifier";
  const belongsToOrg =
    project.data !== undefined &&
    (me.data?.memberships.some((m) => m.org_id === project.data?.org_id) ?? false);

  return (
    <div className="surface-light min-h-full bg-bg p-4 text-fg md:p-8">
      <div className="mx-auto max-w-4xl space-y-5">
        <nav aria-label="Breadcrumb" className="text-sm">
          <Link
            href={`/console/projects/${r.project_id}/reports`}
            className="text-fg-muted underline underline-offset-2"
          >
            {project.data ? `${project.data.name}: MRV reports` : "MRV reports"}
          </Link>
        </nav>
        <header>
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-ok">MRV report</p>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <h1 className="font-display text-3xl font-semibold">
              Version {r.version}, {r.period_start_year}–{r.period_end_year}
            </h1>
            <StatusPill status={st.status}>{st.label}</StatusPill>
          </div>
          <p className="mt-1 text-sm text-fg-muted">
            {period
              ? `${humanize(period.kind)} period ${formatDate(period.start_date)} to ${formatDate(period.end_date)} · `
              : ""}
            Generated {formatDate(r.generated_at)}
          </p>
        </header>

        <p
          role="note"
          className="rounded-lg border border-review-amber/70 bg-review-amber/14 px-3 py-2 text-sm text-warn"
        >
          {ESTIMATE_LABEL}.
        </p>

        <Card title="Key figures" meta="Computed by the carbon engine from field plots">
          <dl className="grid grid-cols-2 gap-6">
            <Stat
              label="lCCC (long-term)"
              value={formatNumber(r.lccc_units)}
              unit="units"
              hint="Whole tCO₂e, rounded down"
              provenance={{ label: "Estimated, testnet" }}
            />
            <Stat
              label="tCCC (temporary)"
              value={formatNumber(r.tccc_units)}
              unit="units"
              hint="Whole tCO₂e, rounded down"
              provenance={{ label: "Estimated, testnet" }}
            />
          </dl>
        </Card>

        <Traceability report={r} />
        <VerifyPanel reportId={r.id} reportHash={r.report_hash} />
        <AnchoringPanel report={r} />
        <Downloads report={r} />
        <ReviewHistory periodId={r.period_id} currentId={r.id} />

        {isVerifier && r.status === "under_review" && !demo && (
          <ReviewPanel
            reportId={r.id}
            projectId={r.project_id}
            periodId={r.period_id}
            belongsToOrg={belongsToOrg}
          />
        )}
        {demo && (
          <p className="text-xs text-fg-muted">
            Demo data is read-only: reviewing reports is disabled.
          </p>
        )}
      </div>
    </div>
  );
}
