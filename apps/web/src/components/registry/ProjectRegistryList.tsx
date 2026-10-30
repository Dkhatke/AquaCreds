"use client";

import type { ProjectOut } from "@aquacreds/api-client";
import Link from "next/link";
import {
  ButtonLink,
  Card,
  type Column,
  DataTable,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusPill,
} from "@/components/ui";
import { formatDate, formatNumber, humanize } from "@/lib/format";
import { useProjects } from "@/lib/queries";
import { useMe, useSession } from "@/lib/registry-queries";
import { METHODOLOGY } from "@/lib/registry-status";
import { PROJECT_STATUS } from "@/lib/status";
import { BoundaryThumb } from "./BoundaryThumb";

export function DocumentStatus({ project }: { project: ProjectOut }) {
  const n = project.documents.length;
  if (n === 0) return <StatusPill status="review">No documents</StatusPill>;
  return (
    <StatusPill status="ok">
      {n} document{n === 1 ? "" : "s"} attached
    </StatusPill>
  );
}

const columns: Column<ProjectOut>[] = [
  {
    key: "boundary",
    header: <span className="sr-only">Boundary</span>,
    cell: (p) => <BoundaryThumb geometry={p.boundary} name={p.name} />,
  },
  {
    key: "name",
    header: "Project",
    cell: (p) => (
      <div>
        <Link
          href={`/console/registry/${p.id}`}
          className="font-medium underline-offset-2 hover:underline"
        >
          {p.name}
        </Link>
        <div className="text-xs text-fg-muted">
          {humanize(p.ecosystem)} · {p.state}
        </div>
      </div>
    ),
  },
  {
    key: "status",
    header: "Status",
    cell: (p) => (
      <StatusPill status={PROJECT_STATUS[p.status].status}>
        {PROJECT_STATUS[p.status].label}
      </StatusPill>
    ),
  },
  { key: "area", header: "Area (ha)", numeric: true, cell: (p) => formatNumber(p.area_ha, 2) },
  { key: "method", header: "Methodology", cell: (p) => METHODOLOGY[p.methodology] },
  { key: "docs", header: "Tenure documents", cell: (p) => <DocumentStatus project={p} /> },
  { key: "start", header: "Start", cell: (p) => formatDate(p.start_date) },
];

export function ProjectRegistryList() {
  const { data, isPending, isError, error, refetch } = useProjects();
  const me = useMe();
  const session = useSession();
  const isAdmin = me.data?.memberships.some((m) => m.role === "admin") ?? false;
  const demo = session.data?.demo ?? false;

  return (
    <div className="mx-auto max-w-6xl p-4 md:p-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl font-semibold">Project registry</h1>
          <p className="mt-2 max-w-2xl text-fg-muted">
            Projects, their boundaries and tenure-document status. Registering a project needs a
            boundary and tenure documents; the registry checks overlaps and methodology rules when
            you submit.
          </p>
        </div>
        {isAdmin && !demo ? (
          <ButtonLink href="/console/registry/new">Register a project</ButtonLink>
        ) : (
          <p className="max-w-xs text-xs text-fg-muted">
            {demo
              ? "The demo is read-only: projects cannot be registered here."
              : "Only organisation admins can register projects."}
          </p>
        )}
      </div>
      <Card
        className="mt-6"
        title="Projects you can access"
        meta="Source: GET /api/v1/projects · boundary area is geodesic (PostGIS)"
      >
        {isPending ? (
          <LoadingState label="Loading projects" />
        ) : isError ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : data.length === 0 ? (
          <EmptyState title="No projects yet">
            Projects appear here once your organisation registers a boundary.
          </EmptyState>
        ) : (
          <DataTable
            caption="Projects"
            hideCaption
            columns={columns}
            rows={data}
            rowKey={(p) => p.id}
          />
        )}
      </Card>
    </div>
  );
}
