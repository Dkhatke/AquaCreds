"use client";

import type { ProjectOut } from "@aquacreds/api-client";
import Link from "next/link";
import {
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
import { PROJECT_STATUS } from "@/lib/status";

const columns: Column<ProjectOut>[] = [
  {
    key: "name",
    header: "Project",
    cell: (p) => (
      <Link
        href={`/console/projects/${p.id}/monitoring`}
        className="font-medium text-fg underline-offset-2 hover:underline"
      >
        {p.name}
      </Link>
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
  { key: "ecosystem", header: "Ecosystem", cell: (p) => humanize(p.ecosystem) },
  { key: "state", header: "State", cell: (p) => p.state },
  { key: "area", header: "Area (ha)", numeric: true, cell: (p) => formatNumber(p.area_ha, 2) },
  { key: "start", header: "Start", cell: (p) => formatDate(p.start_date) },
];

export function ProjectList({ title, intro }: { title: string; intro: string }) {
  const { data, isPending, isError, error, refetch } = useProjects();
  return (
    <div className="mx-auto max-w-6xl p-4 md:p-8">
      <h1 className="font-display text-3xl font-semibold">{title}</h1>
      <p className="mt-2 max-w-2xl text-fg-muted">{intro}</p>
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
            Projects appear here once your organisation registers a boundary, or once you are added
            to an organisation that has one.
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
