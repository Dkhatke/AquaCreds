import Link from "next/link";
import { TxLink } from "@/components/chain/TxLink";
import { StatusPill } from "@/components/ui";
import { explorerUrlFor } from "@/lib/chain-links";
import { formatDate, formatNumber, humanize, shortHash } from "@/lib/format";
import type { ListingRow } from "@/lib/registry-queries";
import { METHODOLOGY } from "@/lib/registry-status";

type Listing = ListingRow;

export const UNAVAILABLE: Record<NonNullable<Listing["unavailable_reason"]>, string> = {
  sold_out: "Sold out",
  cancelled: "Cancelled",
  expired: "Vintage expired",
  insufficient_balance: "Seller balance too low",
  not_approved: "Seller has not approved the marketplace",
  unknown: "Unavailable",
};

export function ListingStewardship({ s }: { s: Listing["stewardship"] }) {
  if (!s) return <span className="text-fg-muted">Not scored</span>;
  if (s.insufficient_data || s.score === null) {
    return <span className="text-fg-muted">Insufficient data · {s.score_version}</span>;
  }
  return (
    <span>
      <span className="tabular font-medium">{formatNumber(s.score, 1)}</span> / 100 ·{" "}
      {s.score_version}
    </span>
  );
}

/** One marketplace listing with the facts a buyer needs: project, MRV report hash, stewardship. */
export function ListingCard({
  listing: l,
  children,
}: {
  listing: Listing;
  children?: React.ReactNode;
}) {
  const pf = l.project_facts;
  const name = l.project.name ?? pf?.name ?? `Project ${shortHash(l.project.project_key, 6, 4)}`;
  const unavailable = l.available === false;
  const report = l.vintage.mrv_report_id;
  return (
    <article
      className="rounded-[var(--radius-card)] border border-line bg-panel p-5"
      aria-label={`Listing ${l.listing_id}: ${name}`}
    >
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-base font-semibold leading-tight">
            {l.project.id ? (
              <Link
                href={`/registry/projects/${l.project.id}`}
                className="underline-offset-2 hover:underline"
              >
                {name}
              </Link>
            ) : (
              name
            )}
          </h3>
          {pf && (
            <p className="mt-1 text-xs text-fg-muted">
              {humanize(pf.ecosystem)} · {pf.state} · {formatNumber(pf.area_ha, 1)} ha ·{" "}
              {METHODOLOGY[pf.methodology]}
            </p>
          )}
        </div>
        {unavailable ? (
          <StatusPill status="review">
            {l.unavailable_reason ? UNAVAILABLE[l.unavailable_reason] : "Unavailable"}
          </StatusPill>
        ) : (
          <StatusPill status="ok">Available</StatusPill>
        )}
      </header>

      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 text-sm md:grid-cols-4">
        <div>
          <dt className="text-xs uppercase tracking-wide text-fg-muted">Price per unit</dt>
          <dd className="tabular font-display text-xl font-semibold">{l.price_per_unit.decimal}</dd>
          <dd className="text-xs text-fg-muted">mock stablecoin (testnet)</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-fg-muted">Remaining</dt>
          <dd className="tabular font-display text-xl font-semibold">
            {formatNumber(l.remaining)}
          </dd>
          <dd className="text-xs text-fg-muted">of {formatNumber(l.amount_listed)} listed</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-fg-muted">Vintage</dt>
          <dd>
            {l.vintage.credit_unit} · token {l.vintage.token_id}
          </dd>
          <dd className="text-xs text-fg-muted">
            {l.vintage.credit_type === "long_term"
              ? "Long-term"
              : l.vintage.expires_at
                ? `Expires ${formatDate(l.vintage.expires_at)}`
                : "Temporary"}
          </dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-fg-muted">Stewardship score</dt>
          <dd>
            <ListingStewardship s={l.stewardship} />
          </dd>
        </div>
      </dl>

      <dl className="mt-4 space-y-1 border-t border-line pt-3 text-xs text-fg-muted">
        <div className="flex flex-wrap gap-x-2">
          <dt>MRV report hash:</dt>
          <dd>
            {l.vintage.report_hash ? (
              <code className="tabular break-all text-fg" title={l.vintage.report_hash}>
                {shortHash(l.vintage.report_hash, 12, 8)}
              </code>
            ) : (
              "not linked to a registry report"
            )}
            {l.vintage.report_hash && (
              <>
                {" "}
                ·{" "}
                <Link href={`/registry/verify?hash=${l.vintage.report_hash}`} className="underline">
                  Verify
                </Link>
              </>
            )}
            {report && (
              <>
                {" "}
                ·{" "}
                <a className="underline" href={`/bff/api/v1/public/reports/${report}/report.pdf`}>
                  Report PDF
                </a>
              </>
            )}
          </dd>
        </div>
        <div className="flex flex-wrap items-center gap-x-2">
          <dt>Listed on-chain:</dt>
          <dd>
            <TxLink
              chainId={l.listed.chain_id}
              txHash={l.listed.tx_hash}
              explorerUrl={explorerUrlFor(l.listed.chain_id, l.listed.explorer_url)}
            />
          </dd>
        </div>
        <p>
          Credit units are estimated, pending accredited verification; testnet registry units.
          Trades are demonstration transactions.
        </p>
      </dl>
      {children && <div className="mt-4">{children}</div>}
    </article>
  );
}
