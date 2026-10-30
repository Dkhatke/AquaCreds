"use client";

import type { Schema } from "@aquacreds/api-client";
import { useState } from "react";
import { ProjectSelect } from "@/components/registry/ProjectSelect";
import { Card, DataTable, EmptyState, ErrorState, LoadingState, Stat } from "@/components/ui";
import { formatDate, formatNumber } from "@/lib/format";
import { useProjectMarket, useTrades } from "@/lib/registry-queries";
import { PriceChart, type PricePointView } from "./PriceChart";

type Trade = Schema<"TradeOut">;

function TradesTable({ trades }: { trades: Trade[] }) {
  return (
    <DataTable
      caption="Executed trades"
      hideCaption
      rows={trades}
      rowKey={(t) => `${t.chain.tx_hash}-${t.chain.log_index}`}
      empty="No trades yet."
      columns={[
        {
          key: "when",
          header: "Time",
          cell: (t) => (t.block_time ? formatDate(t.block_time) : "—"),
        },
        { key: "proj", header: "Project", cell: (t) => t.project?.name ?? "—" },
        { key: "tok", header: "Token", numeric: true, cell: (t) => t.token_id },
        { key: "amt", header: "Units", numeric: true, cell: (t) => formatNumber(t.amount) },
        {
          key: "price",
          header: "Price / unit",
          numeric: true,
          cell: (t) => t.price_per_unit.decimal,
        },
        { key: "total", header: "Total", numeric: true, cell: (t) => t.total_price.decimal },
        { key: "fee", header: "Fee", numeric: true, cell: (t) => t.fee.decimal },
      ]}
    />
  );
}

const toView = (time: string | null, value: string, amount: number): PricePointView | null =>
  time && Number.isFinite(Number(value)) ? { time, value: Number(value), amount } : null;

function Body({ projectId }: { projectId: string }) {
  const market = useProjectMarket(projectId || null);
  const trades = useTrades(projectId || null);
  const q = projectId ? market : trades;
  if (q.isPending) return <LoadingState label="Loading trades" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;

  const rows: Trade[] = projectId ? (market.data?.trades ?? []) : (trades.data?.items ?? []);
  const points = (
    projectId
      ? (market.data?.price_history ?? []).map((p) =>
          toView(p.time, p.price_per_unit.decimal, p.amount),
        )
      : rows.map((t) => toView(t.block_time, t.price_per_unit.decimal, t.amount))
  ).filter((p): p is PricePointView => p !== null);

  return (
    <div className="space-y-6">
      {projectId && market.data && (
        <dl className="grid grid-cols-2 gap-4 md:grid-cols-3">
          <Stat
            label="Last price"
            value={market.data.last_price?.decimal ?? null}
            unit="mock stablecoin"
          />
          <Stat label="Volume" value={formatNumber(market.data.volume_units)} unit="units" />
          <Stat label="Active listings" value={String(market.data.active_listings.length)} />
        </dl>
      )}
      <Card
        title="Price history"
        meta="Source: executed trades indexed from the marketplace contract"
      >
        {points.length === 0 ? (
          <EmptyState title="No price history yet">
            A price appears after the first trade.
          </EmptyState>
        ) : (
          <PriceChart points={points} label="Price per unit" />
        )}
      </Card>
      <Card title="Trades">
        <TradesTable trades={rows} />
      </Card>
    </div>
  );
}

export function TradesPanel({ allowProjectSelect = true }: { allowProjectSelect?: boolean }) {
  const [projectId, setProjectId] = useState("");
  return (
    <div className="space-y-4">
      {allowProjectSelect && (
        <ProjectSelect value={projectId} onChange={setProjectId} allLabel="All projects" />
      )}
      <Body projectId={projectId} />
    </div>
  );
}
