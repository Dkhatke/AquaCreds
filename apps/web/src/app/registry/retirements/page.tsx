import type { Metadata } from "next";
import Link from "next/link";
import { Pager, pageParam } from "@/components/public/Pager";
import { LoadFailed, PageHeader } from "@/components/public/PublicBits";
import { Card, DataTable } from "@/components/ui";
import { formatDate, formatNumber, shortHash } from "@/lib/format";
import { PAGE_SIZE, publicApi } from "@/lib/public-registry";
import { CARBON_LABEL } from "@/lib/registry-status";

export const metadata: Metadata = { title: "Retirements" };

export default async function RetirementsPage({
  searchParams,
}: {
  searchParams: Promise<{ page?: string }>;
}) {
  const page = pageParam((await searchParams).page);
  const r = await publicApi.retirements(page);
  return (
    <>
      <PageHeader eyebrow="Public registry" title="Retirements">
        A retirement permanently takes credits out of circulation. Each has a public certificate
        with a QR code. {CARBON_LABEL}.
      </PageHeader>
      {!r.ok ? (
        <LoadFailed result={r} what="retirements" />
      ) : (
        <Card>
          <DataTable
            caption={`${r.data.total} retirements`}
            rows={r.data.items}
            rowKey={(x) => String(x.retirement_id)}
            empty="No credits have been retired yet."
            columns={[
              {
                key: "id",
                header: "Retirement",
                numeric: true,
                cell: (x) => (
                  <Link
                    className="underline underline-offset-2"
                    href={`/registry/retirements/${x.retirement_id}`}
                  >
                    {x.retirement_id}
                  </Link>
                ),
              },
              { key: "p", header: "Project", cell: (x) => x.project?.name ?? "—" },
              { key: "a", header: "Units", numeric: true, cell: (x) => formatNumber(x.amount) },
              {
                key: "b",
                header: "Beneficiary",
                cell: (x) => (
                  <code className="text-xs" title={x.beneficiary}>
                    {shortHash(x.beneficiary, 6, 4)}
                  </code>
                ),
              },
              { key: "pu", header: "Purpose", cell: (x) => x.purpose },
              {
                key: "t",
                header: "When",
                cell: (x) => (x.block_time ? formatDate(x.block_time) : "—"),
              },
            ]}
          />
          <Pager
            page={page}
            pageSize={PAGE_SIZE}
            total={r.data.total}
            href={(n) => `/registry/retirements?page=${n}`}
          />
        </Card>
      )}
    </>
  );
}
