import type { Metadata } from "next";
import Link from "next/link";
import { Pager, pageParam } from "@/components/public/Pager";
import { LoadFailed, PageHeader } from "@/components/public/PublicBits";
import { Card, DataTable, StatusPill } from "@/components/ui";
import { formatDate, formatNumber } from "@/lib/format";
import { PAGE_SIZE, publicApi } from "@/lib/public-registry";
import { CARBON_LABEL } from "@/lib/registry-status";

export const metadata: Metadata = { title: "Vintages" };

export default async function VintagesPage({
  searchParams,
}: {
  searchParams: Promise<{ page?: string }>;
}) {
  const page = pageParam((await searchParams).page);
  const r = await publicApi.vintages(page);
  return (
    <>
      <PageHeader eyebrow="Public registry" title="Credit vintages">
        Issued tCCC (temporary) and lCCC (long-term) credit tokens. {CARBON_LABEL}.
      </PageHeader>
      {!r.ok ? (
        <LoadFailed result={r} what="vintages" />
      ) : (
        <Card>
          <DataTable
            caption={`${r.data.total} vintages`}
            rows={r.data.items}
            rowKey={(v) => String(v.token_id)}
            empty="No credits have been issued yet."
            columns={[
              { key: "t", header: "Token", numeric: true, cell: (v) => v.token_id },
              {
                key: "p",
                header: "Project",
                cell: (v) =>
                  v.project.id ? (
                    <Link
                      className="underline underline-offset-2"
                      href={`/registry/projects/${v.project.id}`}
                    >
                      {v.project.name ?? v.project.project_key}
                    </Link>
                  ) : (
                    (v.project.name ?? v.project.project_key)
                  ),
              },
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
            ]}
          />
          <Pager
            page={page}
            pageSize={PAGE_SIZE}
            total={r.data.total}
            href={(n) => `/registry/vintages?page=${n}`}
          />
        </Card>
      )}
    </>
  );
}
