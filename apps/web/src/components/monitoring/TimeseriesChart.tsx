"use client";

import type { Schema } from "@aquacreds/api-client";
import { useEffect, useId, useRef, useState } from "react";
import { DataTable } from "@/components/ui";
import { formatDate, formatNumber, formatPercent } from "@/lib/format";

type Point = Schema<"TimeseriesPoint">;

export interface ChartPoint {
  t: number;
  date: string;
  p10: number;
  p50: number;
  p90: number;
  quality: number | null;
}

/** Points with a median value; p10/p90 fall back to the median when missing. */
export function toChartPoints(points: Point[]): ChartPoint[] {
  return points
    .filter((p) => p.p50 !== null || p.mean !== null)
    .map((p) => {
      const mid = (p.p50 ?? p.mean) as number;
      return {
        t: Date.parse(`${p.date}T00:00:00Z`),
        date: p.date,
        p10: p.p10 ?? mid,
        p50: mid,
        p90: p.p90 ?? mid,
        quality: p.clear_fraction ?? p.valid_fraction ?? null,
      };
    })
    .sort((a, b) => a.t - b.t);
}

export function niceTicks(min: number, max: number, count = 4): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (min === max) return [min];
  const raw = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const start = Math.ceil(min / step) * step;
  const ticks: number[] = [];
  for (let v = start; v <= max + step * 1e-9; v += step) ticks.push(Number(v.toFixed(10)));
  return ticks;
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(360);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(
      ([entry]) => entry && setWidth(Math.max(240, entry.contentRect.width)),
    );
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

/** Median line with a p10–p90 band per observation. Accessible: titled SVG + a data table. */
export function TimeseriesChart({
  points,
  label,
  units,
  qualityLabel,
}: {
  points: ChartPoint[];
  label: string;
  units: string;
  qualityLabel: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const titleId = useId();
  const height = 190;
  const m = { top: 10, right: 12, bottom: 24, left: 44 };
  const iw = width - m.left - m.right;
  const ih = height - m.top - m.bottom;

  const tMin = Math.min(...points.map((p) => p.t));
  const tMax = Math.max(...points.map((p) => p.t));
  const vMin = Math.min(...points.map((p) => p.p10));
  const vMax = Math.max(...points.map((p) => p.p90));
  const pad = (vMax - vMin) * 0.08 || Math.abs(vMax) * 0.1 || 0.1;
  const y0 = vMin - pad;
  const y1 = vMax + pad;
  const x = (t: number) => m.left + (tMax === tMin ? iw / 2 : ((t - tMin) / (tMax - tMin)) * iw);
  const y = (v: number) => m.top + ih - ((v - y0) / (y1 - y0)) * ih;

  const line = points
    .map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.p50).toFixed(1)}`)
    .join("");
  const band =
    points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.p90).toFixed(1)}`).join("") +
    [...points]
      .reverse()
      .map((p) => `L${x(p.t).toFixed(1)},${y(p.p10).toFixed(1)}`)
      .join("") +
    "Z";
  const yTicks = niceTicks(y0, y1, 4);
  const first = points[0];
  const last = points[points.length - 1];

  return (
    <div ref={ref}>
      <svg width={width} height={height} role="img" aria-labelledby={titleId} className="block">
        <title id={titleId}>
          {`${label}: ${points.length} observations${first && last ? ` from ${formatDate(first.date)} to ${formatDate(last.date)}` : ""}. Data table below.`}
        </title>
        {yTicks.map((v) => (
          <g key={v}>
            <line
              x1={m.left}
              x2={width - m.right}
              y1={y(v)}
              y2={y(v)}
              stroke="var(--line)"
              strokeWidth={1}
            />
            <text
              x={m.left - 6}
              y={y(v)}
              dy="0.32em"
              textAnchor="end"
              className="tabular fill-[var(--fg-muted)] text-[10px]"
            >
              {formatNumber(v, Math.abs(y1 - y0) < 1 ? 2 : 1)}
            </text>
          </g>
        ))}
        <path d={band} fill="var(--color-lagoon-mint)" fillOpacity={0.16} />
        <path
          d={line}
          fill="none"
          stroke="var(--color-lagoon-mint)"
          strokeWidth={1.75}
          strokeLinejoin="round"
        />
        {points.map((p) => (
          <circle key={p.date} cx={x(p.t)} cy={y(p.p50)} r={2.4} fill="var(--color-lagoon-mint)" />
        ))}
        {first && (
          <text x={m.left} y={height - 6} className="tabular fill-[var(--fg-muted)] text-[10px]">
            {formatDate(first.date)}
          </text>
        )}
        {last && last !== first && (
          <text
            x={width - m.right}
            y={height - 6}
            textAnchor="end"
            className="tabular fill-[var(--fg-muted)] text-[10px]"
          >
            {formatDate(last.date)}
          </text>
        )}
      </svg>
      <p className="mt-1 text-[11px] text-fg-muted">
        Line: median over the project polygon · band: 10th–90th percentile · units: {units}
      </p>
      <details className="mt-2 text-sm">
        <summary className="cursor-pointer text-xs text-fg-muted hover:text-fg">
          Show data table
        </summary>
        <DataTable
          className="mt-2 max-h-56 overflow-y-auto"
          caption={`${label} observations (${units})`}
          rows={points}
          rowKey={(p) => p.date}
          columns={[
            { key: "date", header: "Date", cell: (p) => formatDate(p.date) },
            { key: "p50", header: "Median", numeric: true, cell: (p) => formatNumber(p.p50, 3) },
            { key: "p10", header: "P10", numeric: true, cell: (p) => formatNumber(p.p10, 3) },
            { key: "p90", header: "P90", numeric: true, cell: (p) => formatNumber(p.p90, 3) },
            {
              key: "q",
              header: qualityLabel,
              numeric: true,
              cell: (p) => (p.quality === null ? "—" : formatPercent(p.quality, 0)),
            },
          ]}
        />
      </details>
    </div>
  );
}
