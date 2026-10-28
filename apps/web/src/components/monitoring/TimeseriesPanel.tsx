"use client";

import { useId, useState } from "react";
import { Card, EmptyState, ErrorState, LoadingState, ProvenanceNote, Tabs } from "@/components/ui";
import { formatPercent } from "@/lib/format";
import { type SeriesIndex, useTimeseries } from "@/lib/queries";
import { TimeseriesChart, toChartPoints } from "./TimeseriesChart";

const SERIES: { id: SeriesIndex; label: string; long: string }[] = [
  { id: "ndvi", label: "NDVI", long: "Normalised difference vegetation index (Sentinel-2)" },
  { id: "mvi", label: "MVI", long: "Mangrove vegetation index (Sentinel-2)" },
  { id: "vh_db", label: "SAR VH", long: "Sentinel-1 VH backscatter" },
];

const THRESHOLDS = [0, 0.3, 0.6, 0.9];

function Series({
  projectId,
  index,
  minClear,
}: {
  projectId: string;
  index: SeriesIndex;
  minClear: number;
}) {
  const q = useTimeseries(projectId, index, minClear);
  if (q.isPending) return <LoadingState label="Loading time series" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const ts = q.data;
  const points = toChartPoints(ts.points);
  const sar = ts.sensor === "sentinel-1-rtc";
  const meta = SERIES.find((s) => s.id === index);
  return (
    <div>
      {points.length === 0 ? (
        <EmptyState title="No observations pass this filter">
          {ts.points.length > 0
            ? `${ts.points.length} observations exist but none has a value at ≥ ${formatPercent(minClear, 0)} ${sar ? "valid" : "clear"} pixels.`
            : "Ingestion has not produced observations for this project yet (backfill runs after registration)."}
        </EmptyState>
      ) : (
        <TimeseriesChart
          points={points}
          label={meta?.long ?? ts.index}
          units={ts.units}
          qualityLabel={sar ? "Valid px" : "Clear px"}
        />
      )}
      <ProvenanceNote
        className="mt-3"
        source={sar ? "Sentinel-1 RTC (Planetary Computer)" : "Sentinel-2 L2A, SCL cloud-masked"}
        label="Context only"
      >
        {ts.note}
      </ProvenanceNote>
    </div>
  );
}

export function TimeseriesPanel({ projectId }: { projectId: string }) {
  const [minClear, setMinClear] = useState(0.6);
  const selectId = useId();
  return (
    <Card
      title="Index time series"
      meta="Per-scene statistics over the project polygon"
      actions={
        <div className="flex items-center gap-2">
          <label htmlFor={selectId} className="text-xs text-fg-muted">
            Clear-sky filter
          </label>
          <select
            id={selectId}
            value={minClear}
            onChange={(e) => setMinClear(Number(e.target.value))}
            className="h-8 rounded-md border border-line bg-deep-ocean px-1.5 text-xs text-fg"
          >
            {THRESHOLDS.map((t) => (
              <option key={t} value={t}>
                {t === 0 ? "Off" : `≥ ${formatPercent(t, 0)}`}
              </option>
            ))}
          </select>
        </div>
      }
    >
      <Tabs
        label="Index"
        items={SERIES.map((s) => ({
          id: s.id,
          label: s.label,
          content: <Series projectId={projectId} index={s.id} minClear={minClear} />,
        }))}
      />
    </Card>
  );
}
