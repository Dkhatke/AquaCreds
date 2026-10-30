"use client";

import type { ProjectOut, Schema } from "@aquacreds/api-client";
import { useTranslations } from "next-intl";
import { type FormEvent, useState } from "react";
import { HonestyNote } from "@/components/i18n/HonestyNote";
import {
  Button,
  Card,
  type Column,
  DataTable,
  EmptyState,
  ErrorState,
  LoadingState,
  ProvenanceNote,
} from "@/components/ui";
import { errorMessage } from "@/lib/api-browser";
import { formatDate, formatNumber, humanize } from "@/lib/format";
import { type PeriodActivity, periodActivity, trustBands } from "@/lib/overview-data";
import {
  useAddMember,
  useMe,
  useMyOrgs,
  useOrgMembers,
  useOrgStewardship,
  useProjectSummaries,
} from "@/lib/overview-queries";
import { useProjects } from "@/lib/queries";

type Role = Schema<"OrgRole">;
const ROLES: Role[] = ["admin", "field_agent", "member"];

export function CommunityView() {
  const t = useTranslations("community");
  const me = useMe();
  const orgs = useMyOrgs();
  const projects = useProjects();
  const [chosen, setChosen] = useState<string | null>(null);

  const orgList = orgs.data ?? [];
  const orgId = chosen ?? orgList[0]?.id ?? null;
  const isAdmin =
    me.data?.memberships.some((m) => m.org_id === orgId && m.role === "admin") ?? false;
  const orgProjects = (projects.data ?? []).filter((p) => p.org_id === orgId);

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 md:p-8">
      <div>
        <h1 className="font-display text-3xl font-semibold">{t("title")}</h1>
        <p className="mt-2 max-w-2xl text-fg-muted">{t("intro")}</p>
        {orgList.length > 1 && (
          <select
            aria-label={t("members.title")}
            value={orgId ?? ""}
            onChange={(e) => setChosen(e.target.value)}
            className="mt-3 h-9 rounded-lg border border-line bg-deep-ocean px-2 text-sm"
          >
            {orgList.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </select>
        )}
        {orgList.length === 1 && <p className="mt-2 text-sm font-medium">{orgList[0]?.name}</p>}
      </div>

      {orgs.isPending || me.isPending ? (
        <LoadingState label={t("title")} />
      ) : orgs.isError ? (
        <ErrorState error={orgs.error} onRetry={() => void orgs.refetch()} />
      ) : !orgId ? (
        <EmptyState title={t("members.empty")} />
      ) : (
        <>
          <MembersCard orgId={orgId} isAdmin={isAdmin} />
          <FieldActivity projects={orgProjects} loadingProjects={projects.isPending} />
          <PlainStewardship orgId={orgId} />
        </>
      )}
      <TipsCard />
    </div>
  );
}

function MembersCard({ orgId, isAdmin }: { orgId: string; isAdmin: boolean }) {
  const t = useTranslations("community.members");
  const members = useOrgMembers(isAdmin ? orgId : null);
  const columns: Column<Schema<"MemberOut">>[] = [
    { key: "name", header: t("name"), cell: (m) => m.full_name },
    { key: "email", header: t("email"), cell: (m) => m.email },
    { key: "role", header: t("role"), cell: (m) => t(`roles.${m.role}`) },
  ];
  return (
    <Card title={t("title")} meta={t("meta")}>
      {!isAdmin ? (
        <p className="text-sm text-fg-muted">{t("adminOnly")}</p>
      ) : members.isPending ? (
        <LoadingState label={t("title")} />
      ) : members.isError ? (
        <ErrorState error={members.error} onRetry={() => void members.refetch()} />
      ) : (
        <>
          <DataTable
            caption={t("title")}
            hideCaption
            columns={columns}
            rows={members.data}
            rowKey={(m) => m.user_id}
            empty={t("empty")}
          />
          <AddMember orgId={orgId} />
        </>
      )}
    </Card>
  );
}

function AddMember({ orgId }: { orgId: string }) {
  const t = useTranslations("community.members");
  const add = useAddMember(orgId);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("field_agent");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    add.mutate({ email: email.trim(), role }, { onSuccess: () => setEmail("") });
  };
  return (
    <form onSubmit={submit} className="mt-6 border-t border-line pt-4">
      <h3 className="text-sm font-semibold">{t("inviteTitle")}</h3>
      <p className="mt-1 text-xs text-fg-muted">{t("inviteHelp")}</p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor="member-email" className="block text-xs font-medium">
            {t("emailLabel")}
          </label>
          <input
            id="member-email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 h-9 w-64 rounded-lg border border-line bg-deep-ocean px-3 text-sm"
          />
        </div>
        <div>
          <label htmlFor="member-role" className="block text-xs font-medium">
            {t("roleLabel")}
          </label>
          <select
            id="member-role"
            value={role}
            onChange={(e) => setRole(e.target.value as Role)}
            className="mt-1 h-9 rounded-lg border border-line bg-deep-ocean px-2 text-sm"
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {t(`roles.${r}`)}
              </option>
            ))}
          </select>
        </div>
        <Button type="submit" size="sm" disabled={add.isPending}>
          {t("add")}
        </Button>
      </div>
      {add.isError && (
        <p role="alert" className="mt-2 text-sm text-fail">
          {errorMessage(add.error)}
        </p>
      )}
      {add.isSuccess && (
        <p role="status" className="mt-2 text-sm text-ok">
          {t("added", { name: add.data.full_name })}
        </p>
      )}
    </form>
  );
}

function FieldActivity({
  projects,
  loadingProjects,
}: {
  projects: ProjectOut[];
  loadingProjects: boolean;
}) {
  const t = useTranslations("community.field");
  const queries = useProjectSummaries(projects);
  const pending = loadingProjects || queries.some((q) => q.isPending);
  const summaries = queries.flatMap((q) => (q.data ? [q.data] : []));
  const activity = periodActivity(summaries);
  const failedParts = summaries.filter((s) => !s.submissions.ok).length;
  const all = summaries.flatMap((s) =>
    s.submissions.ok ? s.submissions.data.flatMap((p) => p.items) : [],
  );
  const bands = trustBands(all);
  const max = Math.max(1, ...bands.map((b) => b.count));
  const scored = bands.reduce((n, b) => n + b.count, 0);

  const columns: Column<PeriodActivity>[] = [
    {
      key: "project",
      header: t("period"),
      cell: (r) => (
        <>
          <span className="font-medium">{r.projectName}</span>
          <span className="block text-xs text-fg-muted">
            {humanize(r.period.kind)} · {formatDate(r.period.start_date)} –{" "}
            {formatDate(r.period.end_date)}
          </span>
        </>
      ),
    },
    { key: "total", header: t("submissions"), numeric: true, cell: (r) => r.total },
    { key: "checked", header: t("checked"), numeric: true, cell: (r) => r.checked },
    { key: "rejected", header: t("rejected"), numeric: true, cell: (r) => r.rejected },
    { key: "pending", header: t("pending"), numeric: true, cell: (r) => r.pending },
  ];

  return (
    <Card title={t("title")} meta={t("meta")}>
      <p className="mb-4 rounded-lg border border-line px-3 py-2 text-xs text-fg-muted">
        {t("perAgentGap")}
      </p>
      {pending ? (
        <LoadingState label={t("title")} />
      ) : (
        <div className="space-y-6">
          {failedParts > 0 && (
            <ErrorState
              title={t("title")}
              error={`${failedParts} project(s) could not be loaded`}
            />
          )}
          <DataTable
            caption={t("title")}
            hideCaption
            columns={columns}
            rows={activity}
            rowKey={(r) => r.period.id}
            empty={t("noPeriods")}
          />
          <div>
            <h3 className="text-sm font-semibold">{t("trustTitle")}</h3>
            <p className="mt-1 text-xs text-fg-muted">{t("trustHelp")}</p>
            {scored === 0 ? (
              <p className="mt-3 text-sm text-fg-muted">{t("noSubmissions")}</p>
            ) : (
              <ol className="mt-3 space-y-1.5">
                {bands.map((b) => (
                  <li key={b.from} className="flex items-center gap-3 text-sm">
                    <span className="tabular w-16 shrink-0 text-fg-muted">
                      {t("band", { from: b.from, to: b.to })}
                    </span>
                    <span
                      className="h-3 flex-1 overflow-hidden rounded bg-panel-raised"
                      aria-hidden="true"
                    >
                      <span
                        className="block h-full rounded bg-lagoon-mint"
                        style={{ width: `${(b.count / max) * 100}%` }}
                      />
                    </span>
                    <span className="tabular w-8 text-right">{b.count}</span>
                  </li>
                ))}
              </ol>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

function PlainStewardship({ orgId }: { orgId: string }) {
  const t = useTranslations("community.stewardship");
  const q = useOrgStewardship(orgId);
  return (
    <Card title={t("title")} meta="GET /api/v1/orgs/{org_id}/stewardship">
      <p className="text-sm text-fg-muted">{t("intro")}</p>
      {q.isPending ? (
        <LoadingState label={t("title")} />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : (
        <div className="mt-4 space-y-5">
          {q.data.projects.map((p) => (
            <section key={p.project_id} aria-label={p.project_id}>
              <p className="tabular text-sm">
                <span className="font-display text-2xl font-semibold">
                  {p.score === null ? "—" : formatNumber(p.score, 1)}
                </span>
                {p.score !== null && <span className="ml-1 text-fg-muted">/ 100</span>}
              </p>
              <ul className="mt-2 divide-y divide-line">
                {p.components.map((c) => (
                  <li key={c.name} className="py-2.5">
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="text-sm font-medium">
                        {t.has(`names.${c.name}`) ? t(`names.${c.name}`) : humanize(c.name)}
                      </span>
                      <span className="tabular text-sm">
                        {c.score === null ? "—" : t("score", { score: formatNumber(c.score, 1) })}
                        <span className="ml-2 text-xs text-fg-muted">
                          {t("weight", { weight: formatNumber(c.weight, 2) })}
                        </span>
                      </span>
                    </div>
                    <p className="mt-0.5 text-sm text-fg-muted">
                      {t.has(`componentHelp.${c.name}`)
                        ? t(`componentHelp.${c.name}`)
                        : t("componentHelp.default")}
                    </p>
                    {c.notes.map((n) => (
                      <p key={n} className="mt-0.5 text-xs text-fg-muted">
                        {n}
                      </p>
                    ))}
                  </li>
                ))}
              </ul>
              <ProvenanceNote
                className="mt-2"
                modelVersion={p.score_version}
                asOf={formatDate(p.computed_at)}
              />
            </section>
          ))}
          <HonestyNote kind="carbon" className="text-xs text-fg-muted" />
        </div>
      )}
    </Card>
  );
}

function TipsCard() {
  const t = useTranslations("community.tips");
  return (
    <Card title={t("title")} meta={t("meta")}>
      <ul className="list-disc space-y-2 pl-5 text-sm">
        {[1, 2, 3, 4, 5, 6].map((n) => (
          <li key={n}>{t(`tip${n}`)}</li>
        ))}
      </ul>
    </Card>
  );
}
