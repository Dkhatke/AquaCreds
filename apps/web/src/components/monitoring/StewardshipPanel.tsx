"use client";

import {
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  ProvenanceNote,
  StatusPill,
} from "@/components/ui";
import { formatDate, formatNumber, humanize } from "@/lib/format";
import { useStewardship } from "@/lib/queries";

export function StewardshipPanel({ projectId }: { projectId: string }) {
  const q = useStewardship(projectId);
  return (
    <Card
      title="Stewardship score"
      meta="0–100, recomputed on request from MRV, evidence and satellite tables"
    >
      {q.isPending ? (
        <LoadingState label="Computing stewardship score" />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : (
        <div className="space-y-4">
          <div className="flex items-end gap-3">
            {q.data.score === null ? (
              <EmptyState className="flex-1" title="Not enough data for a score yet">
                {q.data.notes[0] ?? "Each component needs its own minimum history (see below)."}
              </EmptyState>
            ) : (
              <>
                <p className="tabular font-display text-5xl font-semibold leading-none">
                  {formatNumber(q.data.score, 1)}
                  <span className="ml-1 text-base font-normal text-fg-muted">/ 100</span>
                </p>
                {q.data.renormalised && (
                  <StatusPill status="review">Renormalised over available components</StatusPill>
                )}
              </>
            )}
          </div>
          <ul className="divide-y divide-line">
            {q.data.components.map((c) => (
              <li key={c.name} className="py-2.5">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-sm font-medium">{humanize(c.name)}</span>
                  <span className="tabular text-sm">
                    {c.score === null ? (
                      <span className="text-fg-muted">insufficient data</span>
                    ) : (
                      formatNumber(c.score, 1)
                    )}
                    <span className="ml-2 text-xs text-fg-muted">
                      weight {formatNumber(c.weight, 2)}
                    </span>
                  </span>
                </div>
                {Object.keys(c.sub_scores).length > 0 && (
                  <p className="tabular mt-0.5 text-xs text-fg-muted">
                    {Object.entries(c.sub_scores)
                      .map(
                        ([k, v]) =>
                          `${humanize(k).toLowerCase()} ${v === null ? "n/a" : formatNumber(v, 1)}`,
                      )
                      .join(" · ")}
                  </p>
                )}
                {c.notes.map((n) => (
                  <p key={n} className="mt-0.5 text-xs text-fg-muted">
                    {n}
                  </p>
                ))}
              </li>
            ))}
          </ul>
          <ProvenanceNote
            modelVersion={q.data.score_version}
            asOf={formatDate(q.data.computed_at)}
          />
        </div>
      )}
    </Card>
  );
}
