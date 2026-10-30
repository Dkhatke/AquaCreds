"use client";

import type { Schema } from "@aquacreds/api-client";
import { Card, ErrorState, LoadingState, StatusPill } from "@/components/ui";
import { formatDate, shortHash } from "@/lib/format";
import { chainName, explorerTxUrl } from "@/lib/mrv";
import { useChainTx } from "@/lib/queries";

const TX_STATUS = {
  confirmed: "ok",
  failed: "failed",
  reverted: "failed",
  queued: "review",
  waiting_for_gas: "review",
  submitted: "review",
} as const;

function TxRow({ label, txId }: { label: string; txId: string | null }) {
  const tx = useChainTx(txId);
  return (
    <li className="py-2.5">
      <p className="text-sm font-medium">{label}</p>
      {txId === null ? (
        <p className="text-sm text-fg-muted">Not sent yet</p>
      ) : tx.isPending ? (
        <LoadingState label="Loading transaction" />
      ) : tx.isError ? (
        <ErrorState error={tx.error} onRetry={() => void tx.refetch()} />
      ) : (
        <div className="mt-1 space-y-1 text-sm">
          <StatusPill status={TX_STATUS[tx.data.status]}>
            {tx.data.status.replace("_", " ")}
          </StatusPill>
          {tx.data.tx_hash ? (
            <p className="tabular break-all text-xs">
              {(() => {
                const url = explorerTxUrl(tx.data.chain_id, tx.data.tx_hash);
                return url ? (
                  <a
                    href={url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="underline underline-offset-2"
                    title={tx.data.tx_hash}
                  >
                    {shortHash(tx.data.tx_hash, 10, 8)}
                    <span className="sr-only"> (opens the Amoy block explorer in a new tab)</span>
                  </a>
                ) : (
                  <span title={tx.data.tx_hash}>{shortHash(tx.data.tx_hash, 10, 8)}</span>
                );
              })()} <span className="text-fg-muted">
                · {chainName(tx.data.chain_id)}
                {tx.data.block_number !== null && ` · block ${tx.data.block_number}`}
              </span>
            </p>
          ) : (
            <p className="text-xs text-fg-muted">No transaction hash yet.</p>
          )}
          {tx.data.error && <p className="text-xs text-fail">{tx.data.error}</p>}
        </div>
      )}
    </li>
  );
}

export function AnchoringPanel({ report }: { report: Schema<"ReportOut"> }) {
  const anchored = report.anchored_at !== null;
  return (
    <Card
      title="On-chain anchoring"
      meta="The report hash is written to the registry contracts on Polygon Amoy (testnet)"
      actions={
        <StatusPill status={anchored ? "ok" : report.anchor_error ? "failed" : "neutral"}>
          {anchored ? "Anchored" : report.anchor_error ? "Anchoring failed" : "Not anchored"}
        </StatusPill>
      }
    >
      {report.status !== "approved" && !anchored && (
        <p className="mb-2 text-sm text-fg-muted">
          Reports are anchored after a verifier approves them.
        </p>
      )}
      {report.anchor_error && (
        <p role="alert" className="mb-2 text-sm text-fail">
          {report.anchor_error}
        </p>
      )}
      <ul className="divide-y divide-line">
        <TxRow label="Submit report hash" txId={report.submit_tx_id} />
        <TxRow label="Approve report" txId={report.approve_tx_id} />
      </ul>
      <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
        <div>
          <dt className="text-xs uppercase tracking-wide text-fg-muted">On-chain report id</dt>
          <dd className="tabular">{report.chain_report_id ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-fg-muted">Anchored</dt>
          <dd>{report.anchored_at ? formatDate(report.anchored_at) : "—"}</dd>
        </div>
      </dl>
    </Card>
  );
}
