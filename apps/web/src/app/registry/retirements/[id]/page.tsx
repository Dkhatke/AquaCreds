import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { TxLink } from "@/components/chain/TxLink";
import { LoadFailed, PageHeader } from "@/components/public/PublicBits";
import { ButtonLink, Card, Stat } from "@/components/ui";
import { explorerUrlFor } from "@/lib/chain-links";
import { formatDate, formatNumber } from "@/lib/format";
import { publicApi } from "@/lib/public-registry";
import { CARBON_LABEL } from "@/lib/registry-status";

type Props = { params: Promise<{ id: string }> };

export const metadata: Metadata = { title: "Retirement certificate" };

export default async function RetirementPage({ params }: Props) {
  const id = Number((await params).id);
  if (!Number.isSafeInteger(id) || id < 0) notFound();
  const r = await publicApi.retirement(id);
  if (!r.ok && r.status === 404) notFound();
  if (!r.ok) return <LoadFailed result={r} what="this retirement" />;
  const x = r.data;
  const chain = x.verification.chain;
  return (
    <>
      <PageHeader eyebrow="Retirement certificate" title={`Retirement ${x.retirement_id}`}>
        {CARBON_LABEL}. The certificate and QR code below point back to this page, so anyone can
        check the retirement against the chain.
      </PageHeader>
      <div className="grid gap-6 md:grid-cols-[2fr_1fr]">
        <Card title="Details">
          <dl className="grid grid-cols-2 gap-4 md:grid-cols-3">
            <Stat
              label="Units retired"
              value={formatNumber(x.amount)}
              unit={
                x.credit_type === "long_term" ? "lCCC" : x.credit_type === "temporary" ? "tCCC" : ""
              }
              provenance={{ label: "Estimated, pending accredited verification" }}
            />
            <Stat label="Project" value={x.project?.name ?? null} />
            <Stat label="Token" value={String(x.token_id)} />
            <Stat label="Retired on" value={x.block_time ? formatDate(x.block_time) : null} />
            <Stat label="Purpose" value={x.purpose || null} />
            <Stat label="Block" value={formatNumber(x.block_number)} />
          </dl>
          <dl className="mt-5 space-y-2 text-sm">
            <div>
              <dt className="text-xs uppercase tracking-wide text-fg-muted">Beneficiary</dt>
              <dd>
                <code className="break-all text-xs">{x.beneficiary}</code>
              </dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-fg-muted">Transaction</dt>
              <dd>
                <TxLink
                  chainId={chain.chain_id}
                  txHash={x.tx_hash}
                  explorerUrl={explorerUrlFor(chain.chain_id, chain.explorer_url)}
                />
              </dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-fg-muted">
                Certificate payload SHA-256
              </dt>
              <dd>
                <code className="break-all text-xs">{x.verification.payload_sha256}</code>
              </dd>
            </div>
          </dl>
          <div className="mt-5 flex flex-wrap gap-3">
            <ButtonLink
              href={`/bff/api/v1/public/retirements/${x.retirement_id}/certificate.pdf`}
              prefetch={false}
            >
              Download certificate (PDF)
            </ButtonLink>
            {x.project?.id && (
              <ButtonLink variant="secondary" href={`/registry/projects/${x.project.id}`}>
                Project record
              </ButtonLink>
            )}
          </div>
        </Card>
        <Card title="QR code" meta="Scan to open this certificate">
          {/* biome-ignore lint/performance/noImgElement: dynamic API image, fixed size */}
          <img
            src={`/bff/api/v1/public/retirements/${x.retirement_id}/qr.png`}
            alt={`QR code linking to the certificate for retirement ${x.retirement_id}`}
            width={200}
            height={200}
            className="rounded-md border border-line bg-white"
          />
        </Card>
      </div>
      <p className="mt-6 text-sm">
        <Link href="/registry/retirements" className="underline underline-offset-2">
          All retirements
        </Link>
      </p>
    </>
  );
}
