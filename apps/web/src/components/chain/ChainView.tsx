"use client";

import type { Schema } from "@aquacreds/api-client";
import { useEffect, useState } from "react";
import { ProjectSelect } from "@/components/registry/ProjectSelect";
import {
  Card,
  type Column,
  DataTable,
  EmptyState,
  ErrorState,
  LoadingState,
  Stat,
  StatusPill,
} from "@/components/ui";
import { chainLabel, TX_PURPOSE, TX_STATUS } from "@/lib/chain-links";
import { formatDate, formatNumber, shortHash } from "@/lib/format";
import { useProjects } from "@/lib/queries";
import {
  useChainStatus,
  useProjectChain,
  useProjectReports,
  useRetirements,
  useVintages,
} from "@/lib/registry-queries";
import { REPORT_STATUS } from "@/lib/registry-status";
import { TxLink } from "./TxLink";

type Tx = Schema<"ChainTxOut">;

const fmtTime = (iso: string | null) => (iso ? formatDate(iso) : "—");

function TxStatus({ tx }: { tx: Tx }) {
  const s = TX_STATUS[tx.status];
  return (
    <div>
      <StatusPill status={s.status}>{s.label}</StatusPill>
      {tx.status === "waiting_for_gas" && (
        <p className="mt-1 max-w-48 text-xs text-fg-muted">
          The org wallet is below the estimated gas cost. Nothing was broadcast; the gas top-up
          funds it and resubmits.
        </p>
      )}
      {(tx.status === "failed" || tx.status === "reverted") && (tx.error_name || tx.error) && (
        <p className="mt-1 max-w-48 break-words text-xs text-fail">{tx.error_name ?? tx.error}</p>
      )}
    </div>
  );
}

const txColumns: Column<Tx>[] = [
  { key: "purpose", header: "Purpose", cell: (t) => TX_PURPOSE[t.purpose] },
  { key: "fn", header: "Call", cell: (t) => <code className="text-xs">{t.function}</code> },
  { key: "status", header: "Status", cell: (t) => <TxStatus tx={t} /> },
  {
    key: "hash",
    header: "Transaction",
    cell: (t) => <TxLink chainId={t.chain_id} txHash={t.tx_hash} />,
  },
  { key: "signer", header: "Signer", cell: (t) => t.signer },
  { key: "att", header: "Attempts", numeric: true, cell: (t) => t.attempts },
  { key: "block", header: "Block", numeric: true, cell: (t) => t.block_number ?? "—" },
  { key: "at", header: "Created", cell: (t) => fmtTime(t.created_at) },
];

export function ChainView() {
  const projects = useProjects();
  const [projectId, setProjectId] = useState("");
  useEffect(() => {
    if (!projectId && projects.data?.[0]) setProjectId(projects.data[0].id);
  }, [projectId, projects.data]);
  const pid = projectId || null;

  const status = useChainStatus();
  const chain = useProjectChain(pid);
  const reports = useProjectReports(pid);
  const vintages = useVintages(pid);
  const retirements = useRetirements();

  const txs = chain.data?.transactions ?? [];
  const activeChainId =
    status.data?.chain_id ?? txs[0]?.chain_id ?? status.data?.expected_chain_id ?? null;
  const txById = new Map(txs.map((t) => [t.id, t]));
  const relayed = txs.filter((t) => t.purpose !== "gas_top_up");
  const gas = txs.filter((t) => t.purpose === "gas_top_up");
  const registration = txs.find(
    (t) => t.purpose === "register_project" && t.status === "confirmed",
  );
  const myRetirements = (retirements.data?.items ?? []).filter((r) => r.project?.id === pid);

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 md:p-8">
      <header>
        <h1 className="font-display text-3xl font-semibold">Blockchain records</h1>
        <p className="mt-2 max-w-2xl text-fg-muted">
          Project registrations, report anchors, credit issuance and retirements, with every relayed
          transaction and its status. Registry units are on the Polygon Amoy testnet; a chain other
          than Amoy is a local chain and has no public explorer.
        </p>
      </header>

      <Card title="Chain connection" meta="Source: GET /api/v1/chain/status · refreshed every 30 s">
        {status.isPending ? (
          <LoadingState label="Checking the chain" />
        ) : status.isError ? (
          <ErrorState error={status.error} onRetry={() => void status.refetch()} />
        ) : (
          <>
            <dl className="grid grid-cols-2 gap-4 md:grid-cols-4">
              <Stat
                label="Connection"
                value={status.data.connected ? "Connected" : "Not connected"}
                hint={status.data.configured ? undefined : "Chain is not configured"}
              />
              <Stat
                label="Network"
                value={chainLabel(status.data.chain_id ?? status.data.expected_chain_id)}
                hint={`chain id ${status.data.chain_id ?? "unknown"} (expected ${status.data.expected_chain_id})`}
              />
              <Stat
                label="Latest block"
                value={
                  status.data.latest_block === null ? null : formatNumber(status.data.latest_block)
                }
              />
              <Stat label="Contracts" value={String(Object.keys(status.data.contracts).length)} />
            </dl>
            {status.data.error && (
              <p role="alert" className="mt-3 text-sm text-fail">
                {status.data.error}
              </p>
            )}
            {status.data.signers.length > 0 && (
              <DataTable
                className="mt-4"
                caption="Relayer signer wallets"
                columns={[
                  { key: "role", header: "Signer", cell: (s) => String(s.role) },
                  {
                    key: "addr",
                    header: "Address",
                    cell: (s) => (
                      <code className="text-xs" title={s.address}>
                        {shortHash(s.address, 8, 6)}
                      </code>
                    ),
                  },
                  {
                    key: "bal",
                    header: "Gas balance (POL, testnet)",
                    numeric: true,
                    cell: (s) =>
                      s.balance_wei === null ? "—" : formatNumber(s.balance_wei / 1e18, 4),
                  },
                ]}
                rows={status.data.signers}
                rowKey={(s) => s.address}
              />
            )}
          </>
        )}
      </Card>

      <ProjectSelect value={projectId} onChange={setProjectId} />

      {projects.isError ? (
        <ErrorState error={projects.error} onRetry={() => void projects.refetch()} />
      ) : !pid && !projects.isPending ? (
        <EmptyState title="No projects">
          There is nothing on-chain until a project exists.
        </EmptyState>
      ) : (
        <>
          <Card title="Project registration" meta="Source: GET /api/v1/projects/{id}/chain">
            {chain.isPending ? (
              <LoadingState label="Loading chain records" />
            ) : chain.isError ? (
              <ErrorState error={chain.error} onRetry={() => void chain.refetch()} />
            ) : (
              <div className="flex flex-wrap items-center gap-4 text-sm">
                <StatusPill status={chain.data.registered ? "ok" : "neutral"}>
                  {chain.data.registered ? "Registered on-chain" : "Not registered on-chain"}
                </StatusPill>
                <code className="text-xs" title={chain.data.project_key}>
                  key {shortHash(chain.data.project_key, 10, 6)}
                </code>
                {registration && (
                  <TxLink chainId={registration.chain_id} txHash={registration.tx_hash} />
                )}
              </div>
            )}
          </Card>

          <Card
            title="Report submissions and approvals"
            meta="Each MRV report hash is anchored on submission and again on approval. Carbon figures: estimated, pending accredited verification; testnet registry units."
          >
            {reports.isPending ? (
              <LoadingState label="Loading reports" />
            ) : reports.isError ? (
              <ErrorState error={reports.error} onRetry={() => void reports.refetch()} />
            ) : (
              <DataTable
                caption="MRV reports and their anchors"
                hideCaption
                rows={reports.data}
                rowKey={(r) => r.id}
                empty="No reports for this project yet."
                columns={[
                  {
                    key: "period",
                    header: "Period",
                    cell: (r) => `${r.period_start_year}–${r.period_end_year} · v${r.version}`,
                  },
                  {
                    key: "status",
                    header: "Status",
                    cell: (r) => (
                      <StatusPill status={REPORT_STATUS[r.status].status}>
                        {REPORT_STATUS[r.status].label}
                      </StatusPill>
                    ),
                  },
                  {
                    key: "hash",
                    header: "Report hash",
                    cell: (r) => (
                      <code className="text-xs" title={r.report_hash}>
                        {shortHash(r.report_hash, 8, 6)}
                      </code>
                    ),
                  },
                  {
                    key: "submit",
                    header: "Submitted",
                    cell: (r) => (
                      <AnchorTx
                        tx={r.submit_tx_id ? txById.get(r.submit_tx_id) : undefined}
                        error={r.anchor_error}
                      />
                    ),
                  },
                  {
                    key: "approve",
                    header: "Decision",
                    cell: (r) => (
                      <AnchorTx
                        tx={r.approve_tx_id ? txById.get(r.approve_tx_id) : undefined}
                        error={null}
                      />
                    ),
                  },
                  {
                    key: "units",
                    header: "tCCC / lCCC (est.)",
                    numeric: true,
                    cell: (r) => `${formatNumber(r.tccc_units)} / ${formatNumber(r.lccc_units)}`,
                  },
                ]}
              />
            )}
          </Card>

          <Card
            title="Issuance: tCCC and lCCC vintages"
            meta="Source: GET /api/v1/public/vintages · estimated, pending accredited verification; testnet registry units"
          >
            {vintages.isPending ? (
              <LoadingState label="Loading vintages" />
            ) : vintages.isError ? (
              <ErrorState error={vintages.error} onRetry={() => void vintages.refetch()} />
            ) : (
              <DataTable
                caption="Vintages"
                hideCaption
                rows={vintages.data.items}
                rowKey={(v) => String(v.token_id)}
                empty="No credits have been issued for this project."
                columns={[
                  { key: "tok", header: "Token", numeric: true, cell: (v) => v.token_id },
                  { key: "unit", header: "Unit", cell: (v) => v.credit_unit },
                  {
                    key: "issued",
                    header: "Issued",
                    numeric: true,
                    cell: (v) => formatNumber(v.issued),
                  },
                  {
                    key: "ret",
                    header: "Retired",
                    numeric: true,
                    cell: (v) => formatNumber(v.retired),
                  },
                  {
                    key: "out",
                    header: "Outstanding",
                    numeric: true,
                    cell: (v) => formatNumber(v.outstanding),
                  },
                  {
                    key: "exp",
                    header: "Expires",
                    cell: (v) =>
                      v.credit_type === "long_term" ? (
                        "Long-term"
                      ) : v.expires_at ? (
                        <>
                          {formatDate(v.expires_at)}
                          {v.expired && (
                            <StatusPill status="failed" className="ml-2">
                              Expired
                            </StatusPill>
                          )}
                        </>
                      ) : (
                        "—"
                      ),
                  },
                  {
                    key: "tx",
                    header: "Issuance transactions",
                    cell: (v) => (
                      <ul className="space-y-1">
                        {v.issuances.map((i) => (
                          <li key={i.tx_hash}>
                            <TxLink
                              chainId={activeChainId}
                              txHash={i.tx_hash}
                              explorerUrl={i.explorer_url}
                            />
                          </li>
                        ))}
                      </ul>
                    ),
                  },
                ]}
              />
            )}
          </Card>

          <Card
            title="Retirements"
            meta="Source: GET /api/v1/public/retirements (latest 100, filtered to this project)"
          >
            {retirements.isPending ? (
              <LoadingState label="Loading retirements" />
            ) : retirements.isError ? (
              <ErrorState error={retirements.error} onRetry={() => void retirements.refetch()} />
            ) : (
              <DataTable
                caption="Retirements"
                hideCaption
                rows={myRetirements}
                rowKey={(r) => String(r.retirement_id)}
                empty="No credits from this project have been retired."
                columns={[
                  { key: "id", header: "Retirement", numeric: true, cell: (r) => r.retirement_id },
                  {
                    key: "amt",
                    header: "Units",
                    numeric: true,
                    cell: (r) => formatNumber(r.amount),
                  },
                  { key: "purpose", header: "Purpose", cell: (r) => r.purpose },
                  { key: "when", header: "When", cell: (r) => fmtTime(r.block_time) },
                  {
                    key: "cert",
                    header: "Certificate",
                    cell: (r) => (
                      <a
                        className="underline underline-offset-2"
                        href={`/registry/retirements/${r.retirement_id}`}
                      >
                        View
                      </a>
                    ),
                  },
                ]}
              />
            )}
          </Card>

          <Card
            title="Relayed transactions"
            meta="Statuses: queued, waiting for gas, submitted, confirmed, failed, reverted"
          >
            {chain.isPending ? (
              <LoadingState label="Loading transactions" />
            ) : chain.isError ? (
              <ErrorState error={chain.error} onRetry={() => void chain.refetch()} />
            ) : (
              <DataTable
                caption="Relayed transactions"
                hideCaption
                rows={relayed}
                rowKey={(t) => t.id}
                columns={txColumns}
                empty="No transactions relayed for this project."
              />
            )}
          </Card>

          <Card
            title="Gas top-ups"
            meta="The relayer funds custodial org wallets so queued transactions can proceed"
          >
            {chain.isSuccess && (
              <DataTable
                caption="Gas top-ups"
                hideCaption
                rows={gas}
                rowKey={(t) => t.id}
                columns={txColumns}
                empty="No gas top-ups."
              />
            )}
          </Card>
        </>
      )}
    </div>
  );
}

function AnchorTx({ tx, error }: { tx: Tx | undefined; error: string | null }) {
  if (!tx) {
    return error ? (
      <span className="text-xs text-fail">{error}</span>
    ) : (
      <span className="text-fg-muted">—</span>
    );
  }
  const s = TX_STATUS[tx.status];
  return (
    <div className="space-y-1">
      <StatusPill status={s.status}>{s.label}</StatusPill>
      <div>
        <TxLink chainId={tx.chain_id} txHash={tx.tx_hash} />
      </div>
    </div>
  );
}
