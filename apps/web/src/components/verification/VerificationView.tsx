"use client";

import type { Schema } from "@aquacreds/api-client";
import { useId, useMemo, useState } from "react";
import {
  Card,
  type Column,
  DataTable,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusPill,
} from "@/components/ui";
import {
  filterSubmissions,
  NO_FILTER,
  parseChecks,
  type SubmissionFilter,
  worstOutcome,
} from "@/lib/evidence";
import { formatDate, formatNumber, humanize } from "@/lib/format";
import { PERIOD_STATUS } from "@/lib/mrv";
import { usePeriods, usePlots, useProject, useSubmissionsForPeriods } from "@/lib/queries";
import { SubmissionCard } from "./SubmissionCard";

type Plot = Schema<"PlotOut">;
type Submission = Schema<"SubmissionOut">;

function Select<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  const id = useId();
  return (
    <div className="flex items-center gap-2">
      <label htmlFor={id} className="text-xs uppercase tracking-wide text-fg-muted">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value as T)}
        className="h-9 rounded-lg border border-line bg-mangrove px-2 text-sm"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}

function Filters({
  value,
  onChange,
}: {
  value: SubmissionFilter;
  onChange: (f: SubmissionFilter) => void;
}) {
  return (
    <fieldset className="flex flex-wrap gap-x-5 gap-y-2">
      <legend className="sr-only">Filter submissions</legend>
      <Select
        label="Status"
        value={value.status}
        onChange={(status) => onChange({ ...value, status })}
        options={[
          { value: "all", label: "All" },
          { value: "checked", label: "Checked" },
          { value: "pending_checks", label: "Checks running" },
          { value: "rejected", label: "Auto-rejected" },
        ]}
      />
      <Select
        label="Worst result"
        value={value.outcome}
        onChange={(outcome) => onChange({ ...value, outcome })}
        options={[
          { value: "all", label: "Any" },
          { value: "fail", label: "Has a failure" },
          { value: "warn", label: "Has a warning" },
          { value: "pass", label: "All passed" },
        ]}
      />
      <Select
        label="Kind"
        value={value.kind}
        onChange={(kind) => onChange({ ...value, kind })}
        options={[
          { value: "all", label: "All" },
          { value: "plot_centre", label: "Plot centre" },
          { value: "photo_point", label: "Photo point" },
          { value: "general", label: "General" },
        ]}
      />
    </fieldset>
  );
}

function PeriodEvidence({
  period,
  subs,
  plotCodes,
}: {
  period: Schema<"PeriodOut">;
  subs: Submission[];
  plotCodes: Map<string, string>;
}) {
  const [filter, setFilter] = useState<SubmissionFilter>(NO_FILTER);
  const shown = useMemo(() => filterSubmissions(subs, filter), [subs, filter]);
  const ps = PERIOD_STATUS[period.status];
  const flagged = subs.filter((s) => {
    const w = worstOutcome(parseChecks(s.checks));
    return w === "fail" || w === "warn";
  }).length;
  return (
    <Card
      as="section"
      title={
        <>
          {humanize(period.kind)} period · {formatDate(period.start_date)} to{" "}
          {formatDate(period.end_date)}
        </>
      }
      meta={`${subs.length} submission${subs.length === 1 ? "" : "s"} · ${flagged} with warnings or failures`}
      actions={<StatusPill status={ps.status}>{ps.label}</StatusPill>}
    >
      {subs.length === 0 ? (
        <EmptyState title="No photo submissions in this period">
          Field agents upload geotagged photos from the mobile app; they appear here once their
          checks have run.
        </EmptyState>
      ) : (
        <div className="space-y-4">
          <Filters value={filter} onChange={setFilter} />
          <p className="text-xs text-fg-muted" aria-live="polite">
            Showing {shown.length} of {subs.length}
          </p>
          {shown.length === 0 ? (
            <EmptyState title="No submissions match these filters" />
          ) : (
            <ul className="grid gap-4 lg:grid-cols-2">
              {shown.map((s) => (
                <li key={s.id}>
                  <SubmissionCard
                    sub={s}
                    plotCode={s.plot_id ? (plotCodes.get(s.plot_id) ?? null) : null}
                  />
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  );
}

function PlotTable({ plots, subs }: { plots: Plot[]; subs: Submission[] }) {
  const rows = plots.map((p) => {
    const mine = subs.filter((s) => s.plot_id === p.id);
    const scores = mine.map((s) => s.trust_score).filter((x): x is number => x !== null);
    return {
      plot: p,
      photos: mine.length,
      best: scores.length ? Math.max(...scores) : null,
    };
  });
  const columns: Column<(typeof rows)[number]>[] = [
    {
      key: "code",
      header: "Plot",
      cell: (r) => <span className="font-medium">{r.plot.code}</span>,
    },
    { key: "stratum", header: "Stratum", cell: (r) => r.plot.stratum },
    { key: "marker", header: "Marker", cell: (r) => r.plot.marker_id },
    {
      key: "area",
      header: "Area (m²)",
      numeric: true,
      cell: (r) => formatNumber(r.plot.area_m2, 0),
    },
    {
      key: "pos",
      header: "Centre (lat, lon)",
      cell: (r) => `${r.plot.lat.toFixed(5)}, ${r.plot.lon.toFixed(5)}`,
    },
    { key: "photos", header: "Photos", numeric: true, cell: (r) => r.photos },
    {
      key: "trust",
      header: "Best trust",
      numeric: true,
      cell: (r) => (r.best === null ? "—" : r.best),
    },
  ];
  return (
    <Card
      title="Plots"
      meta="Per-tree measurement tables are not exposed by the API yet; this shows the plot register and its photo evidence"
    >
      <DataTable
        caption="Plots with photo evidence"
        hideCaption
        columns={columns}
        rows={rows}
        rowKey={(r) => r.plot.id}
        empty="No plots registered for this project."
      />
    </Card>
  );
}

export function VerificationView({ projectId }: { projectId: string }) {
  const project = useProject(projectId);
  const periods = usePeriods(projectId);
  const plots = usePlots(projectId);
  const sorted = [...(periods.data ?? [])].sort((a, b) => b.start_date.localeCompare(a.start_date));
  const subQs = useSubmissionsForPeriods(sorted.map((p) => p.id));
  const plotCodes = useMemo(
    () => new Map((plots.data ?? []).map((p) => [p.id, p.code])),
    [plots.data],
  );

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
  const allSubs = subQs.flatMap((q) => q.data ?? []);

  return (
    <div className="mx-auto max-w-6xl space-y-5 p-4 md:p-8">
      <header>
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-lagoon-mint">
          AI verification
        </p>
        <h1 className="mt-2 font-display text-3xl font-semibold">{project.data.name}</h1>
        <p className="mt-2 max-w-3xl text-fg-muted">
          Every field photo is checked for EXIF consistency, the live-capture window, the project
          boundary and duplicates, and may get an advisory photo-plausibility signal. The checks
          combine into a trust score.
        </p>
        <p
          role="note"
          className="mt-3 max-w-3xl rounded-lg border border-line bg-panel px-3 py-2 text-sm"
        >
          These checks inform a human verifier. They never approve or reject a report on their own:
          a low score is a reason to look closer, not a verdict.
        </p>
      </header>

      {periods.isPending ? (
        <LoadingState label="Loading monitoring periods" />
      ) : periods.isError ? (
        <ErrorState error={periods.error} onRetry={() => void periods.refetch()} />
      ) : sorted.length === 0 ? (
        <EmptyState title="No monitoring periods yet" />
      ) : (
        sorted.map((period, i) => {
          const q = subQs[i];
          return q?.isPending ? (
            <LoadingState key={period.id} label="Loading submissions" />
          ) : q?.isError ? (
            <ErrorState
              key={period.id}
              title="Submissions could not be loaded"
              error={q.error}
              onRetry={() => void q.refetch()}
            />
          ) : (
            <PeriodEvidence
              key={period.id}
              period={period}
              subs={q?.data ?? []}
              plotCodes={plotCodes}
            />
          );
        })
      )}

      {plots.isError ? (
        <ErrorState title="Plots could not be loaded" error={plots.error} />
      ) : plots.isPending ? (
        <LoadingState label="Loading plots" />
      ) : (
        <PlotTable plots={plots.data} subs={allSubs} />
      )}
    </div>
  );
}
