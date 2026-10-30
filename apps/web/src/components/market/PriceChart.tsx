"use client";

import { useId } from "react";
import { DataTable } from "@/components/ui";
import { formatDate, formatNumber } from "@/lib/format";

export interface PricePointView {
  time: string;
  value: number;
  amount: number;
}

/** Price per unit over time (mock stablecoin, testnet). Titled SVG + a data table. */
export function PriceChart({ points, label }: { points: PricePointView[]; label: string }) {
  const titleId = useId();
  const W = 600;
  const H = 190;
  const m = { top: 12, right: 14, bottom: 26, left: 48 };
  const sorted = [...points].sort((a, b) => a.time.localeCompare(b.time));
  if (sorted.length === 0) return null;
  const ts = sorted.map((p) => Date.parse(p.time));
  const tMin = Math.min(...ts);
  const tMax = Math.max(...ts);
  const vMin = Math.min(...sorted.map((p) => p.value));
  const vMax = Math.max(...sorted.map((p) => p.value));
  const pad = (vMax - vMin) * 0.1 || Math.abs(vMax) * 0.1 || 1;
  const y0 = Math.max(0, vMin - pad);
  const y1 = vMax + pad;
  const x = (t: number) =>
    m.left +
    (tMax === tMin
      ? (W - m.left - m.right) / 2
      : ((t - tMin) / (tMax - tMin)) * (W - m.left - m.right));
  const y = (v: number) => m.top + (H - m.top - m.bottom) * (1 - (v - y0) / (y1 - y0));
  const line = sorted
    .map((p, i) => `${i ? "L" : "M"}${x(Date.parse(p.time)).toFixed(1)},${y(p.value).toFixed(1)}`)
    .join("");
  const ticks = [y0, (y0 + y1) / 2, y1];
  const first = sorted[0];
  const last = sorted[sorted.length - 1];
  return (
    <div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-labelledby={titleId}
        className="block h-auto w-full"
      >
        <title id={titleId}>
          {`${label}: ${sorted.length} trades${first && last ? ` from ${formatDate(first.time)} to ${formatDate(last.time)}` : ""}. Data table below.`}
        </title>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={m.left} x2={W - m.right} y1={y(v)} y2={y(v)} stroke="var(--line)" />
            <text
              x={m.left - 6}
              y={y(v)}
              dy="0.32em"
              textAnchor="end"
              className="tabular fill-[var(--fg-muted)] text-[10px]"
            >
              {formatNumber(v, 2)}
            </text>
          </g>
        ))}
        <path
          d={line}
          fill="none"
          stroke="var(--color-lagoon-mint)"
          strokeWidth={1.75}
          strokeLinejoin="round"
        />
        {sorted.map((p) => (
          <circle
            key={`${p.time}-${p.value}-${p.amount}`}
            cx={x(Date.parse(p.time))}
            cy={y(p.value)}
            r={2.6}
            fill="var(--color-lagoon-mint)"
          />
        ))}
        {first && (
          <text x={m.left} y={H - 7} className="tabular fill-[var(--fg-muted)] text-[10px]">
            {formatDate(first.time)}
          </text>
        )}
        {last && last !== first && (
          <text
            x={W - m.right}
            y={H - 7}
            textAnchor="end"
            className="tabular fill-[var(--fg-muted)] text-[10px]"
          >
            {formatDate(last.time)}
          </text>
        )}
      </svg>
      <p className="mt-1 text-[11px] text-fg-muted">
        Price per unit in the mock testnet stablecoin. Each dot is one executed trade.
      </p>
      <details className="mt-2 text-sm">
        <summary className="cursor-pointer text-xs text-fg-muted hover:text-fg">
          Show data table
        </summary>
        <DataTable
          className="mt-2 max-h-56 overflow-y-auto"
          caption={`${label} (price per unit, mock stablecoin)`}
          rows={sorted}
          rowKey={(p) => `${p.time}-${p.value}-${p.amount}`}
          columns={[
            { key: "t", header: "Time", cell: (p) => formatDate(p.time) },
            { key: "v", header: "Price", numeric: true, cell: (p) => formatNumber(p.value, 4) },
            { key: "a", header: "Units", numeric: true, cell: (p) => formatNumber(p.amount) },
          ]}
        />
      </details>
    </div>
  );
}
