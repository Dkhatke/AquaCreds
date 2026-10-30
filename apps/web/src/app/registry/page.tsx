import Link from "next/link";
import { Pager, pageParam } from "@/components/public/Pager";
import { LoadFailed, PageHeader } from "@/components/public/PublicBits";
import { Card, DataTable, StatusPill } from "@/components/ui";
import { formatDate, formatNumber, humanize } from "@/lib/format";
import { PAGE_SIZE, publicApi } from "@/lib/public-registry";
import { ECOSYSTEMS, METHODOLOGY } from "@/lib/registry-status";
import { PROJECT_STATUS } from "@/lib/status";

type Search = Promise<{ page?: string; state?: string; ecosystem?: string }>;
const one = (v: string | undefined) => v || undefined;

export default async function RegistryProjects({ searchParams }: { searchParams: Search }) {
  const sp = await searchParams;
  const page = pageParam(sp.page);
  const state = one(sp.state);
  const eco = one(sp.ecosystem);
  const ecosystem = ECOSYSTEMS.find((e) => e === eco);
  const r = await publicApi.projects({
    page,
    ...(state ? { state } : {}),
    ...(ecosystem ? { ecosystem } : {}),
  });
  const href = (p: number) => {
    const q = new URLSearchParams();
    if (state) q.set("state", state);
    if (ecosystem) q.set("ecosystem", ecosystem);
    if (p > 1) q.set("page", String(p));
    return `/registry${q.size ? `?${q}` : ""}`;
  };
  return (
    <>
      <PageHeader eyebrow="Public registry" title="Projects">
        Every project here has been submitted to the AquaCreds registry. Open one to see its
        boundary, approved MRV reports and credit vintages. No account needed.
      </PageHeader>
      <form
        method="get"
        action="/registry"
        className="mb-6 flex flex-wrap items-end gap-3"
        aria-label="Filter projects"
      >
        <div>
          <label htmlFor="f-state" className="mb-1 block text-xs font-medium text-fg-muted">
            State
          </label>
          <input
            id="f-state"
            name="state"
            defaultValue={state}
            placeholder="Odisha"
            className="h-10 rounded-lg border border-line bg-panel px-3 text-sm"
          />
        </div>
        <div>
          <label htmlFor="f-eco" className="mb-1 block text-xs font-medium text-fg-muted">
            Ecosystem
          </label>
          <select
            id="f-eco"
            name="ecosystem"
            defaultValue={ecosystem ?? ""}
            className="h-10 rounded-lg border border-line bg-panel px-3 text-sm"
          >
            <option value="">All</option>
            {ECOSYSTEMS.map((e) => (
              <option key={e} value={e}>
                {humanize(e)}
              </option>
            ))}
          </select>
        </div>
        <button
          type="submit"
          className="h-10 rounded-full bg-lagoon-mint px-5 text-sm font-medium text-deep-ocean"
        >
          Filter
        </button>
      </form>
      {!r.ok ? (
        <LoadFailed result={r} what="projects" />
      ) : (
        <Card>
          <DataTable
            caption={`${r.data.total} project${r.data.total === 1 ? "" : "s"}`}
            rows={r.data.items}
            rowKey={(p) => p.id}
            empty="No projects match."
            columns={[
              {
                key: "n",
                header: "Project",
                cell: (p) => (
                  <div>
                    <Link
                      href={`/registry/projects/${p.id}`}
                      className="font-medium underline-offset-2 hover:underline"
                    >
                      {p.name}
                    </Link>
                    <div className="text-xs text-fg-muted">{p.org.name}</div>
                  </div>
                ),
              },
              {
                key: "s",
                header: "Status",
                cell: (p) => (
                  <StatusPill status={PROJECT_STATUS[p.status].status}>
                    {PROJECT_STATUS[p.status].label}
                  </StatusPill>
                ),
              },
              {
                key: "e",
                header: "Ecosystem",
                cell: (p) => `${humanize(p.ecosystem)} · ${p.state}`,
              },
              {
                key: "loc",
                header: "Centroid (lat, lon)",
                numeric: true,
                cell: (p) => `${p.centroid[1]?.toFixed(3)}, ${p.centroid[0]?.toFixed(3)}`,
              },
              {
                key: "a",
                header: "Area (ha)",
                numeric: true,
                cell: (p) => formatNumber(p.area_ha, 2),
              },
              { key: "m", header: "Methodology", cell: (p) => METHODOLOGY[p.methodology] },
              {
                key: "c",
                header: "On-chain",
                cell: (p) =>
                  p.registered_on_chain ? (
                    <StatusPill status="ok">Registered</StatusPill>
                  ) : (
                    <StatusPill status="neutral">Not yet</StatusPill>
                  ),
              },
              { key: "d", header: "Start", cell: (p) => formatDate(p.start_date) },
            ]}
          />
          <Pager page={page} pageSize={PAGE_SIZE} total={r.data.total} href={href} />
        </Card>
      )}
    </>
  );
}
