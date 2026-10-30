"use client";

import type { ProjectOut } from "@aquacreds/api-client";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";
import { HonestyNote } from "@/components/i18n/HonestyNote";
import {
  Card,
  type Column,
  DataTable,
  EmptyState,
  ErrorState,
  LoadingState,
  type Status,
  StatusPill,
} from "@/components/ui";
import { formatDate, formatNumber } from "@/lib/format";
import {
  type Attention,
  buildAttention,
  latestReport,
  type ProjectSummary,
} from "@/lib/overview-data";
import {
  useMe,
  useMfaStatus,
  useOrgStewardship,
  useProjectSummaries,
} from "@/lib/overview-queries";
import { useProjects } from "@/lib/queries";
import { PROJECT_STATUS } from "@/lib/status";
import { type MapProject, OverviewMap } from "./OverviewMap";

export const REPORT_TONE: Record<string, Status> = {
  draft: "neutral",
  under_review: "review",
  changes_requested: "review",
  approved: "ok",
  rejected: "failed",
};

function Unavailable() {
  const t = useTranslations("overview.projects");
  return <span className="text-fg-muted">{t("detailError")}</span>;
}

export function OverviewView() {
  const t = useTranslations("overview");
  const projects = useProjects();
  const me = useMe();
  const mfa = useMfaStatus();
  const list = projects.data ?? [];
  const summaryQueries = useProjectSummaries(list);
  const summaries = summaryQueries.flatMap((q) => (q.data ? [q.data] : []));
  const summariesDone = summaryQueries.every((q) => !q.isPending);
  const byId = useMemo(() => new Map(summaries.map((s) => [s.project.id, s])), [summaries]);

  const mapProjects: MapProject[] = list.map((p) => ({
    id: p.id,
    name: p.name,
    boundary: p.boundary as unknown as MapProject["boundary"],
    tone: PROJECT_STATUS[p.status].status,
  }));

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-4 md:p-8">
      <div>
        <h1 className="font-display text-3xl font-semibold">{t("title")}</h1>
        <p className="mt-2 max-w-2xl text-fg-muted">{t("intro")}</p>
      </div>

      {projects.isPending ? (
        <LoadingState label={t("projects.title")} />
      ) : projects.isError ? (
        <ErrorState error={projects.error} onRetry={() => void projects.refetch()} />
      ) : list.length === 0 ? (
        <EmptyState title={t("projects.empty")}>{t("projects.emptyBody")}</EmptyState>
      ) : (
        <>
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_24rem]">
            <Card title={t("map.title")} className="flex min-h-80 flex-col" as="section">
              <div className="min-h-72 flex-1 overflow-hidden rounded-lg">
                <OverviewMap projects={mapProjects} label={t("map.label")} />
              </div>
            </Card>
            <AttentionCard
              summaries={summaries}
              loading={!summariesDone}
              platformRole={me.data?.platform_role ?? null}
              mfa={mfa.data ?? null}
            />
          </div>
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_24rem]">
            <ProjectsTable projects={list} byId={byId} />
            <OrgScoreCard
              orgIds={[...new Set(list.map((p) => p.org_id))]}
              memberOf={me.data?.memberships.map((m) => m.org_id) ?? []}
            />
          </div>
          <div className="space-y-1 text-xs text-fg-muted">
            <HonestyNote kind="contextOnly" />
            <HonestyNote kind="carbon" />
          </div>
        </>
      )}
    </div>
  );
}

function attentionText(t: ReturnType<typeof useTranslations>, a: Attention): string {
  return t(`attention.${a.kind}`, { count: a.count, name: a.name ?? "" });
}

function AttentionCard({
  summaries,
  loading,
  platformRole,
  mfa,
}: {
  summaries: ProjectSummary[];
  loading: boolean;
  platformRole: Parameters<typeof buildAttention>[0]["platformRole"];
  mfa: Parameters<typeof buildAttention>[0]["mfa"];
}) {
  const t = useTranslations("overview");
  const items = buildAttention({ summaries, platformRole, mfa });
  return (
    <Card title={t("attention.title")} meta={t("attention.meta")}>
      {loading && items.length === 0 ? (
        <LoadingState label={t("attention.title")} />
      ) : items.length === 0 ? (
        <p className="text-sm text-fg-muted">{t("attention.empty")}</p>
      ) : (
        <ul className="space-y-2">
          {items.map((a) => (
            <li
              key={`${a.kind}-${a.name ?? ""}`}
              className="flex items-start justify-between gap-3 rounded-lg border border-line px-3 py-2"
            >
              <span className="text-sm">
                <StatusPill status={a.severity} className="mr-2">
                  {a.severity === "failed" ? t("attention.fix") : t("attention.review")}
                </StatusPill>
                {attentionText(t, a)}
              </span>
              <Link
                href={a.href}
                className="shrink-0 text-sm text-lagoon-mint underline-offset-2 hover:underline"
              >
                {t("attention.open")}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function ProjectsTable({
  projects,
  byId,
}: {
  projects: ProjectOut[];
  byId: Map<string, ProjectSummary>;
}) {
  const t = useTranslations("overview.projects");
  const rs = useTranslations("reportStatus");
  const columns: Column<ProjectOut>[] = [
    {
      key: "name",
      header: t("project"),
      cell: (p) => (
        <Link
          href={`/console/projects/${p.id}/monitoring`}
          className="font-medium underline-offset-2 hover:underline"
        >
          {p.name}
        </Link>
      ),
    },
    {
      key: "status",
      header: t("status"),
      cell: (p) => (
        <StatusPill status={PROJECT_STATUS[p.status].status}>
          {PROJECT_STATUS[p.status].label}
        </StatusPill>
      ),
    },
    {
      key: "ndvi",
      header: t("ndvi"),
      cell: (p) => {
        const s = byId.get(p.id);
        if (!s) return <span className="text-fg-muted">{t("loadingDetail")}</span>;
        if (!s.ndvi.ok) return <Unavailable />;
        return s.ndvi.data ? (
          t("ndviAt", {
            value: formatNumber(s.ndvi.data.value, 2),
            date: formatDate(s.ndvi.data.date),
          })
        ) : (
          <span className="text-fg-muted">{t("noClassification")}</span>
        );
      },
    },
    {
      key: "class",
      header: t("classification"),
      cell: (p) => {
        const s = byId.get(p.id);
        if (!s) return <span className="text-fg-muted">{t("loadingDetail")}</span>;
        if (!s.classification.ok) return <Unavailable />;
        const c = s.classification.data;
        return c ? (
          t("mangroveHa", {
            ha: formatNumber(c.mangrove_ha, 1),
            model: c.model_version,
            year: c.season_start_year,
          })
        ) : (
          <span className="text-fg-muted">{t("noClassification")}</span>
        );
      },
    },
    {
      key: "alerts",
      header: t("alerts"),
      numeric: true,
      cell: (p) => {
        const s = byId.get(p.id);
        if (!s) return "…";
        return s.openAlerts.ok ? s.openAlerts.data.length : <Unavailable />;
      },
    },
    {
      key: "report",
      header: t("report"),
      cell: (p) => {
        const s = byId.get(p.id);
        if (!s) return <span className="text-fg-muted">{t("loadingDetail")}</span>;
        if (!s.reports.ok) return <Unavailable />;
        const r = latestReport(s.reports.data);
        return r ? (
          <StatusPill status={REPORT_TONE[r.status] ?? "neutral"}>{rs(r.status)}</StatusPill>
        ) : (
          <span className="text-fg-muted">{t("noReport")}</span>
        );
      },
    },
  ];
  return (
    <Card title={t("title")} meta={t("meta")}>
      <DataTable
        caption={t("title")}
        hideCaption
        columns={columns}
        rows={projects}
        rowKey={(p) => p.id}
      />
    </Card>
  );
}

function OrgScoreCard({ orgIds, memberOf }: { orgIds: string[]; memberOf: string[] }) {
  const t = useTranslations("overview.stewardship");
  const h = useTranslations();
  const mine = orgIds.filter((id) => memberOf.includes(id));
  const [chosen, setChosen] = useState<string | null>(null);
  const orgId = chosen ?? mine[0] ?? null;
  const q = useOrgStewardship(orgId);
  return (
    <Card title={t("title")} meta={t("meta")}>
      {!orgId ? (
        <p className="text-sm text-fg-muted">{t("noOrg")}</p>
      ) : q.isPending ? (
        <LoadingState label={t("title")} />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : (
        <div className="space-y-3">
          {mine.length > 1 && (
            <select
              aria-label={t("title")}
              value={orgId}
              onChange={(e) => setChosen(e.target.value)}
              className="h-8 rounded-lg border border-line bg-deep-ocean px-2 text-sm"
            >
              {mine.map((id) => (
                <option key={id} value={id}>
                  {id.slice(0, 8)}
                </option>
              ))}
            </select>
          )}
          {q.data.score === null ? (
            <p className="text-sm text-fg-muted">{q.data.notes[0] ?? t("insufficient")}</p>
          ) : (
            <p className="tabular font-display text-5xl font-semibold leading-none">
              {formatNumber(q.data.score, 1)}
              <span className="ml-1 text-base font-normal text-fg-muted">/ 100</span>
            </p>
          )}
          <ul className="divide-y divide-line">
            {Object.entries(q.data.components).map(([name, score]) => (
              <li key={name} className="flex items-baseline justify-between py-2 text-sm">
                <span>
                  {h.has(`community.stewardship.names.${name}`)
                    ? h(`community.stewardship.names.${name}`)
                    : name}
                </span>
                <span className="tabular">
                  {score === null ? (
                    <span className="text-fg-muted">{t("insufficientComponent")}</span>
                  ) : (
                    formatNumber(score, 1)
                  )}
                </span>
              </li>
            ))}
          </ul>
          <p className="text-xs text-fg-muted">
            {t("version", { version: q.data.score_version })} · {formatDate(q.data.computed_at)}
          </p>
          <p className="text-xs text-fg-muted">
            {t("forwardFunding", {
              state: q.data.forward_funding.eligible ? t("eligible") : t("notEligible"),
            })}
          </p>
          <Link
            href="/console/community"
            className="text-sm text-lagoon-mint underline-offset-2 hover:underline"
          >
            {h("console.nav.community")}
          </Link>
        </div>
      )}
    </Card>
  );
}
