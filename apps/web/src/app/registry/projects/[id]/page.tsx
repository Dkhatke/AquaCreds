import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { TxLink } from "@/components/chain/TxLink";
import { LoadFailed, PageHeader } from "@/components/public/PublicBits";
import { BoundaryThumb } from "@/components/registry/BoundaryThumb";
import { Card, DataTable, EmptyState, ProvenanceNote, Stat, StatusPill } from "@/components/ui";
import { explorerUrlFor } from "@/lib/chain-links";
import { formatDate, formatNumber, formatPercent, humanize, shortHash } from "@/lib/format";
import { publicApi } from "@/lib/public-registry";
import { CARBON_LABEL, METHODOLOGY } from "@/lib/registry-status";
import { PROJECT_STATUS } from "@/lib/status";

type Props = { params: Promise<{ id: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  return { title: "Project record", description: `Public record for project ${(await params).id}` };
}

export default async function PublicProjectPage({ params }: Props) {
  const { id } = await params;
  const r = await publicApi.project(id);
  if (!r.ok && r.status === 404) notFound();
  if (!r.ok) return <LoadFailed result={r} what="this project" />;
  const p = r.data;
  const st = PROJECT_STATUS[p.status];
  const chainId = p.registration?.chain_id ?? null;

  return (
    <>
      <PageHeader eyebrow={`${humanize(p.ecosystem)} · ${p.state}`} title={p.name}>
        <span className="flex flex-wrap items-center gap-2">
          <StatusPill status={st.status}>{st.label}</StatusPill>
          {p.org.name} · {METHODOLOGY[p.methodology]}
        </span>
      </PageHeader>

      <div className="grid gap-6 md:grid-cols-[1fr_2fr]">
        <Card
          title="Boundary"
          meta={`${p.boundary.simplification} (tolerance ${p.boundary.tolerance_deg}°). Drawn from the registry geometry.`}
        >
          <BoundaryThumb geometry={p.boundary.geometry} name={p.name} width={280} height={200} />
        </Card>
        <Card title="Project facts">
          <dl className="grid grid-cols-2 gap-4 md:grid-cols-3">
            <Stat
              label="Area"
              value={formatNumber(p.area_ha, 2)}
              unit="ha"
              provenance={{ source: "geodesic area (PostGIS)" }}
            />
            <Stat label="Start date" value={formatDate(p.start_date)} />
            <Stat
              label="Evidence checked"
              value={`${p.evidence.checked} / ${p.evidence.submissions_total}`}
              hint={`${p.evidence.rejected} rejected, ${p.evidence.pending_checks} pending`}
            />
            <Stat
              label="Median photo trust score"
              value={
                p.evidence.median_trust_score === null
                  ? null
                  : formatNumber(p.evidence.median_trust_score, 2)
              }
            />
            <Stat
              label={`Satellite ${p.satellite.index.toUpperCase()} (latest)`}
              value={
                p.satellite.latest_p50 === null ? null : formatNumber(p.satellite.latest_p50, 3)
              }
              provenance={{
                label: "Context only",
                source: "Sentinel-2/1 over the project polygon",
                ...(p.satellite.latest_date ? { asOf: formatDate(p.satellite.latest_date) } : {}),
              }}
            />
            <Stat
              label="Loss alerts"
              value={String(p.alerts.total)}
              hint={`${p.alerts.high} high, ${p.alerts.medium} medium, ${p.alerts.low} low`}
            />
          </dl>
          {p.description && <p className="mt-4 text-sm text-fg-muted">{p.description}</p>}
          <div className="mt-4 text-sm">
            {p.registration ? (
              <p className="flex flex-wrap items-center gap-2">
                <StatusPill status="ok">Registered on-chain</StatusPill>
                boundary hash{" "}
                <code className="text-xs" title={p.registration.boundary_hash}>
                  {shortHash(p.registration.boundary_hash, 10, 6)}
                </code>
                <TxLink
                  chainId={p.registration.chain_id}
                  txHash={p.registration.tx_hash}
                  explorerUrl={explorerUrlFor(p.registration.chain_id, p.registration.explorer_url)}
                />
              </p>
            ) : (
              <StatusPill status="neutral">Not registered on-chain</StatusPill>
            )}
          </div>
        </Card>
      </div>

      <Card
        className="mt-6"
        title="Approved MRV reports"
        meta={`Each report is hashed; the hash can be verified at /registry/verify. ${CARBON_LABEL}.`}
      >
        <DataTable
          caption="Approved MRV reports"
          hideCaption
          rows={p.mrv_reports}
          rowKey={(m) => m.id}
          empty="No MRV report has been approved yet."
          columns={[
            {
              key: "per",
              header: "Period",
              cell: (m) => `${m.period_start_year}–${m.period_end_year} · v${m.version}`,
            },
            {
              key: "hash",
              header: "Report hash",
              cell: (m) => (
                <Link
                  href={`/registry/verify?hash=${m.report_hash}`}
                  className="underline underline-offset-2"
                >
                  <code className="text-xs" title={m.report_hash}>
                    {shortHash(m.report_hash, 10, 6)}
                  </code>
                </Link>
              ),
            },
            {
              key: "t",
              header: "tCCC (est.)",
              numeric: true,
              cell: (m) => formatNumber(m.tccc_units),
            },
            {
              key: "l",
              header: "lCCC (est.)",
              numeric: true,
              cell: (m) => formatNumber(m.lccc_units),
            },
            {
              key: "d",
              header: "Decided",
              cell: (m) => (m.decided_at ? formatDate(m.decided_at) : "—"),
            },
            {
              key: "an",
              header: "Anchored",
              cell: (m) =>
                m.anchoring.hash_anchored && m.anchoring.submit_tx ? (
                  <TxLink
                    chainId={m.anchoring.submit_tx.chain_id}
                    txHash={m.anchoring.submit_tx.tx_hash}
                    explorerUrl={explorerUrlFor(
                      m.anchoring.submit_tx.chain_id,
                      m.anchoring.submit_tx.explorer_url,
                    )}
                  />
                ) : (
                  <StatusPill status="review">Not anchored</StatusPill>
                ),
            },
            {
              key: "dl",
              header: "Download",
              cell: (m) => (
                <span className="flex gap-3">
                  <a
                    className="underline underline-offset-2"
                    href={`/bff/api/v1/public/reports/${m.id}/report.pdf`}
                  >
                    PDF
                    <span className="sr-only">
                      {" "}
                      for {m.period_start_year}–{m.period_end_year}
                    </span>
                  </a>
                  <a
                    className="underline underline-offset-2"
                    href={`/bff/api/v1/public/reports/${m.id}/record.json`}
                  >
                    Record JSON
                    <span className="sr-only">
                      {" "}
                      for {m.period_start_year}–{m.period_end_year}
                    </span>
                  </a>
                </span>
              ),
            },
          ]}
        />
        <ProvenanceNote
          className="mt-3"
          label="Estimated — pending accredited verification"
          source="carbon engine from field plots; methodology profile in each record"
        />
      </Card>

      <Card
        className="mt-6"
        title="Credit vintages"
        meta={`tCCC = temporary credit, lCCC = long-term credit. ${CARBON_LABEL}.`}
      >
        {p.vintages.length === 0 ? (
          <EmptyState title="No credits issued">
            Credits are issued only after a report is approved.
          </EmptyState>
        ) : (
          <DataTable
            caption="Vintages"
            hideCaption
            rows={p.vintages}
            rowKey={(v) => String(v.token_id)}
            columns={[
              { key: "tok", header: "Token", numeric: true, cell: (v) => v.token_id },
              { key: "u", header: "Unit", cell: (v) => v.credit_unit },
              { key: "i", header: "Issued", numeric: true, cell: (v) => formatNumber(v.issued) },
              { key: "r", header: "Retired", numeric: true, cell: (v) => formatNumber(v.retired) },
              {
                key: "o",
                header: "Outstanding",
                numeric: true,
                cell: (v) => formatNumber(v.outstanding),
              },
              {
                key: "e",
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
                key: "x",
                header: "Issuance tx",
                cell: (v) => (
                  <ul className="space-y-1">
                    {v.issuances.map((i) => (
                      <li key={i.tx_hash}>
                        <TxLink chainId={chainId} txHash={i.tx_hash} explorerUrl={i.explorer_url} />
                      </li>
                    ))}
                  </ul>
                ),
              },
            ]}
          />
        )}
      </Card>

      <Card className="mt-6" title="Monitoring periods">
        <DataTable
          caption="Monitoring periods"
          hideCaption
          rows={p.periods}
          rowKey={(x) => x.id}
          empty="No monitoring periods."
          columns={[
            { key: "k", header: "Kind", cell: (x) => humanize(x.kind) },
            { key: "s", header: "From", cell: (x) => formatDate(x.start_date) },
            { key: "e", header: "To", cell: (x) => formatDate(x.end_date) },
            { key: "st", header: "Status", cell: (x) => humanize(x.status) },
          ]}
        />
        {p.satellite.latest_clear_fraction !== null && (
          <p className="mt-3 text-xs text-fg-muted">
            Latest satellite observation clear-sky fraction:{" "}
            {formatPercent(p.satellite.latest_clear_fraction, 0)}.
          </p>
        )}
      </Card>
    </>
  );
}
