"use client";

import type { Schema } from "@aquacreds/api-client";
import { useState } from "react";
import { WalletGate } from "@/components/registry/WalletGate";
import {
  Button,
  Card,
  type Column,
  DataTable,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusPill,
} from "@/components/ui";
import { formatNumber, shortHash } from "@/lib/format";
import { useForwardRequest, useForwardRequests } from "@/lib/registry-queries";

type Summary = Schema<"RequestSummaryOut">;
const REQUEST_STATUS = { open: "review", awarded: "ok", settled: "neutral" } as const;

/** The disclaimer is always visible: upfront payments can be lost if a seller does not deliver. */
export function BuyerRiskNotice({ apiText }: { apiText?: string | undefined }) {
  return (
    <div
      role="note"
      className="rounded-lg border border-review-amber/60 bg-review-amber/10 px-4 py-3 text-sm"
    >
      <p className="font-medium text-warn">Buyer risk</p>
      <p className="mt-1">
        Forward funding pays for removals that have not happened yet. An upfront payment is at the
        buyer's risk: it may not be refundable if the project does not deliver verified credits by
        the deadline. Units are testnet registry units; payments use a mock stablecoin, so
        everything here is a demonstration.
      </p>
      {apiText && <p className="mt-2 text-xs text-fg-muted">From the API: {apiText}</p>}
    </div>
  );
}

function Detail({ id }: { id: number }) {
  const q = useForwardRequest(id);
  if (q.isPending) return <LoadingState label="Loading request" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const d = q.data;
  return (
    <div className="space-y-5">
      {d.upfront.buyer_risk && (
        <p role="note" className="text-sm text-warn">
          This request pays {d.upfront.share_pct} % upfront (
          {d.upfront.refundable ? "refundable" : "not refundable"}
          ). {d.upfront.note}
        </p>
      )}
      <DataTable
        caption="Offers from restoration projects"
        rows={d.offers}
        rowKey={(o) => String(o.offer_id)}
        empty="No offers yet."
        columns={[
          { key: "id", header: "Offer", numeric: true, cell: (o) => o.offer_id },
          {
            key: "seller",
            header: "Seller",
            cell: (o) => <code className="text-xs">{shortHash(o.seller, 6, 4)}</code>,
          },
          { key: "q", header: "Units", numeric: true, cell: (o) => formatNumber(o.quantity) },
          {
            key: "p",
            header: "Price / unit",
            numeric: true,
            cell: (o) => formatNumber(o.price_per_unit),
          },
          {
            key: "s",
            header: "Status",
            cell: (o) => (
              <StatusPill
                status={
                  o.status === "awarded" ? "ok" : o.status === "active" ? "review" : "neutral"
                }
              >
                {o.status.replace("_", " ")}
              </StatusPill>
            ),
          },
        ]}
      />
      <DataTable
        caption="Awards"
        rows={d.awards}
        rowKey={(a) => String(a.award_id)}
        empty="Nothing awarded yet."
        columns={[
          { key: "id", header: "Award", numeric: true, cell: (a) => a.award_id },
          { key: "q", header: "Units", numeric: true, cell: (a) => formatNumber(a.quantity) },
          {
            key: "del",
            header: "Delivered",
            numeric: true,
            cell: (a) => formatNumber(a.delivered_quantity),
          },
          {
            key: "open",
            header: "Open",
            numeric: true,
            cell: (a) => formatNumber(a.open_quantity),
          },
          {
            key: "paid",
            header: "Upfront paid",
            numeric: true,
            cell: (a) => formatNumber(a.upfront_paid),
          },
          {
            key: "esc",
            header: "Escrow left",
            numeric: true,
            cell: (a) => formatNumber(a.escrow_left),
          },
          {
            key: "risk",
            header: "Buyer risk",
            cell: (a) =>
              a.upfront_buyer_risk ? <StatusPill status="review">Upfront at risk</StatusPill> : "—",
          },
          { key: "set", header: "Settled", cell: (a) => (a.settled ? "Yes" : "No") },
        ]}
      />
      <WalletGate action="Posting a request or an offer" />
    </div>
  );
}

export function ForwardFunding() {
  const q = useForwardRequests();
  const [selected, setSelected] = useState<number | null>(null);

  const columns: Column<Summary>[] = [
    { key: "id", header: "Request", numeric: true, cell: (r) => r.request_id },
    {
      key: "status",
      header: "Status",
      cell: (r) => <StatusPill status={REQUEST_STATUS[r.status]}>{r.status}</StatusPill>,
    },
    { key: "q", header: "Units wanted", numeric: true, cell: (r) => formatNumber(r.quantity) },
    {
      key: "max",
      header: "Max price / unit",
      numeric: true,
      cell: (r) => formatNumber(r.max_price),
    },
    { key: "offers", header: "Active offers", numeric: true, cell: (r) => r.active_offers },
    {
      key: "awarded",
      header: "Awarded",
      numeric: true,
      cell: (r) => formatNumber(r.awarded_quantity),
    },
    {
      key: "del",
      header: "Delivered",
      numeric: true,
      cell: (r) => formatNumber(r.delivered_quantity),
    },
    {
      key: "up",
      header: "Upfront",
      cell: (r) =>
        r.upfront.buyer_risk ? (
          <StatusPill status="review">{r.upfront.share_pct} % at risk</StatusPill>
        ) : (
          "None"
        ),
    },
    {
      key: "open",
      header: <span className="sr-only">Details</span>,
      cell: (r) => (
        <Button
          size="sm"
          variant="secondary"
          aria-pressed={selected === r.request_id}
          onClick={() => setSelected(selected === r.request_id ? null : r.request_id)}
        >
          {selected === r.request_id ? "Hide" : "Offers and awards"}
          <span className="sr-only"> for request {r.request_id}</span>
        </Button>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <BuyerRiskNotice apiText={q.data?.disclaimer} />
      <Card
        title="Funding requests"
        meta="Source: GET /api/v1/forward-fund/requests · reverse auction: sellers offer to deliver future verified removals"
      >
        {q.isPending ? (
          <LoadingState label="Loading requests" />
        ) : q.isError ? (
          <ErrorState error={q.error} onRetry={() => void q.refetch()} />
        ) : q.data.items.length === 0 ? (
          <EmptyState title="No funding requests">
            Requests appear when a buyer opens one.
          </EmptyState>
        ) : (
          <DataTable
            caption="Forward-funding requests"
            hideCaption
            columns={columns}
            rows={q.data.items}
            rowKey={(r) => String(r.request_id)}
          />
        )}
      </Card>
      {selected !== null && (
        <Card title={`Request ${selected}: offers and awards`}>
          <Detail id={selected} />
        </Card>
      )}
    </div>
  );
}
