"use client";

import { type ProjectOut, unwrap } from "@aquacreds/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useMemo, useState } from "react";
import { Button, ErrorState } from "@/components/ui";
import { browserClient, errorMessage } from "@/lib/api-browser";
import { formatNumber } from "@/lib/format";
import { defaultStrata, type StratumInput, validateStrata } from "@/lib/mrv";
import { keys } from "@/lib/queries";

/**
 * Organisation-admin form that queues a report. The API needs the hectares of every stratum measured
 * in the period; with a single stratum it defaults to the whole project area.
 */
export function BuildReportForm({
  project,
  periodId,
  strata,
  onClose,
}: {
  project: ProjectOut;
  periodId: string;
  /** Strata of the project's plots (the API does not list per-period measurements). */
  strata: string[];
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const uid = useId();
  const [rows, setRows] = useState<StratumInput[]>(() => defaultStrata(strata, project.area_ha));
  const [includeSoc, setIncludeSoc] = useState(true);
  const [notes, setNotes] = useState("");
  const result = useMemo(() => validateStrata(rows, project.area_ha), [rows, project.area_ha]);

  const build = useMutation({
    mutationFn: async () => {
      if (!result.payload) throw new Error("Fix the stratum areas first");
      await unwrap(
        browserClient().api.POST("/api/v1/periods/{period_id}/reports", {
          params: { path: { period_id: periodId } },
          body: {
            strata_area_ha: result.payload,
            include_soc: includeSoc,
            notes: notes.trim() === "" ? null : notes.trim(),
          },
        }),
      );
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: keys.periods(project.id) });
      await qc.invalidateQueries({ queryKey: keys.reports(periodId) });
      onClose();
    },
  });

  const update = (i: number, patch: Partial<StratumInput>) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  return (
    <form
      className="mt-4 space-y-4 rounded-lg border border-line bg-panel-raised/50 p-4"
      aria-label="Build report"
      onSubmit={(e) => {
        e.preventDefault();
        if (result.payload) build.mutate();
      }}
    >
      <p className="text-sm text-fg-muted">
        The carbon engine builds the report from this period's plot measurements. Give the area each
        measured stratum represents; together they may not exceed the project area of{" "}
        <span className="tabular font-medium text-fg">{formatNumber(project.area_ha, 2)} ha</span>.
      </p>

      {rows.length === 0 ? (
        <p role="status" className="text-sm text-warn">
          This project has no plots yet, so there is nothing to report on. Record plots and
          measurements first.
        </p>
      ) : (
        <fieldset className="space-y-2">
          <legend className="text-xs font-medium uppercase tracking-wide text-fg-muted">
            Strata measured in this period
          </legend>
          {rows.map((r, i) => (
            <div key={r.stratum} className="flex flex-wrap items-center gap-3">
              <label className="flex min-w-40 items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={r.included}
                  onChange={(e) => update(i, { included: e.target.checked })}
                />
                {r.stratum}
              </label>
              <label className="flex items-center gap-2 text-sm">
                <span className="text-fg-muted">Area (ha)</span>
                <input
                  type="number"
                  inputMode="decimal"
                  min="0"
                  step="any"
                  value={r.area}
                  disabled={!r.included}
                  aria-label={`Area of stratum ${r.stratum} in hectares`}
                  onChange={(e) => update(i, { area: e.target.value })}
                  className="tabular h-9 w-32 rounded-lg border border-line bg-panel px-2 text-right disabled:opacity-50"
                />
              </label>
            </div>
          ))}
          <p className="tabular text-xs text-fg-muted" aria-live="polite">
            Total {formatNumber(result.total, 2)} of {formatNumber(project.area_ha, 2)} ha
          </p>
        </fieldset>
      )}

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={includeSoc}
          onChange={(e) => setIncludeSoc(e.target.checked)}
        />
        Include soil organic carbon
      </label>

      <div>
        <label htmlFor={`${uid}-notes`} className="text-sm text-fg-muted">
          Notes for the verifier (optional)
        </label>
        <textarea
          id={`${uid}-notes`}
          value={notes}
          maxLength={2000}
          rows={2}
          onChange={(e) => setNotes(e.target.value)}
          className="mt-1 w-full rounded-lg border border-line bg-panel p-2 text-sm"
        />
      </div>

      {result.errors.length > 0 && rows.length > 0 && (
        <ul role="alert" className="list-disc space-y-0.5 pl-5 text-sm text-fail">
          {result.errors.map((m) => (
            <li key={m}>{m}</li>
          ))}
        </ul>
      )}
      {build.isError && (
        <ErrorState title="The report could not be queued" error={errorMessage(build.error)} />
      )}

      <div className="flex gap-2">
        <Button type="submit" disabled={!result.payload || build.isPending}>
          {build.isPending ? "Queuing…" : "Build report"}
        </Button>
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
