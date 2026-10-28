"use client";

import type { AlertOut } from "@aquacreds/api-client";
import { Card, EmptyState, ErrorState, LoadingState, StatusPill } from "@/components/ui";
import { formatDate, formatNumber, humanize } from "@/lib/format";
import { useAlerts } from "@/lib/queries";
import { ALERT_SEVERITY } from "@/lib/status";

function expectedText(expected: AlertOut["expected"]): string | null {
  const parts = Object.entries(expected)
    .filter(([, v]) => typeof v === "number" || typeof v === "string")
    .slice(0, 3)
    .map(
      ([k, v]) => `${humanize(k).toLowerCase()} ${typeof v === "number" ? formatNumber(v, 3) : v}`,
    );
  return parts.length ? parts.join(", ") : null;
}

export function AlertsTimeline({ projectId }: { projectId: string }) {
  const q = useAlerts(projectId);
  return (
    <Card title="Alerts" meta="Loss and change alerts from nightly ingestion">
      {q.isPending ? (
        <LoadingState label="Loading alerts" />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : q.data.length === 0 ? (
        <EmptyState title="No alerts">
          No vegetation, SAR or mangrove-area alerts have been raised for this project.
        </EmptyState>
      ) : (
        <ol className="relative space-y-4 border-l border-line pl-4">
          {[...q.data]
            .sort((a, b) => b.observed_on.localeCompare(a.observed_on))
            .map((a) => {
              const expected = expectedText(a.expected);
              return (
                <li key={a.id} className="relative">
                  <span
                    aria-hidden="true"
                    className="absolute -left-[21px] top-1.5 size-2.5 rounded-full border-2 border-deep-ocean"
                    style={{
                      background:
                        a.severity === "high"
                          ? "#C95A52"
                          : a.severity === "medium"
                            ? "#E9AD54"
                            : "#A9C2BB",
                    }}
                  />
                  <div className="flex flex-wrap items-center gap-2">
                    <time dateTime={a.observed_on} className="tabular text-xs text-fg-muted">
                      {formatDate(a.observed_on)}
                    </time>
                    <StatusPill status={ALERT_SEVERITY[a.severity]}>
                      {humanize(a.severity)}
                    </StatusPill>
                    <span className="text-xs text-fg-muted">{humanize(a.kind)}</span>
                  </div>
                  <p className="mt-1 text-sm">{a.message}</p>
                  <p className="tabular mt-0.5 text-xs text-fg-muted">
                    {a.metric}: {formatNumber(a.observed_value, 3)}
                    {expected && ` (expected ${expected})`}
                  </p>
                  <p className="mt-0.5 text-xs">
                    {a.acknowledged_at ? (
                      <span className="text-ok">Acknowledged {formatDate(a.acknowledged_at)}</span>
                    ) : (
                      <span className="text-warn">Not acknowledged</span>
                    )}
                  </p>
                </li>
              );
            })}
        </ol>
      )}
    </Card>
  );
}
