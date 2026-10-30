"use client";

import { unwrap } from "@aquacreds/api-client";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { browserClient } from "./api-browser";
import { type ProjectSummary, pollDelay, settle } from "./overview-data";

export const okeys = {
  me: ["me"] as const,
  mfa: ["mfa-status"] as const,
  unread: ["notifications", "unread"] as const,
  notifications: (unread: boolean, offset: number) =>
    ["notifications", "list", unread, offset] as const,
  prefs: ["notifications", "prefs"] as const,
  summary: (id: string) => ["project-summary", id] as const,
  orgStewardship: (id: string) => ["org-stewardship", id] as const,
  members: (id: string) => ["org-members", id] as const,
};

export function useMe() {
  return useQuery({ queryKey: okeys.me, queryFn: () => browserClient().auth.me() });
}

export function useMfaStatus() {
  return useQuery({ queryKey: okeys.mfa, queryFn: () => browserClient().auth.mfaStatus() });
}

/** Unread count with backoff: 30 s while it changes, doubling to 5 min while it does not or the
 *  call fails. Paused in background tabs; resets on any change and after mark-read. */
export function useUnreadCount() {
  const track = useRef<{ steps: number; last: number | undefined }>({ steps: 0, last: undefined });
  return useQuery({
    queryKey: okeys.unread,
    staleTime: 0,
    retry: false,
    queryFn: async () => {
      const r = await unwrap(browserClient().api.GET("/api/v1/notifications/unread-count"));
      return r.unread;
    },
    refetchInterval: (query) => {
      const t = track.current;
      const data = query.state.data;
      t.steps = query.state.status === "error" || data === t.last ? t.steps + 1 : 0;
      t.last = data;
      return pollDelay(t.steps);
    },
  });
}

export function useNotifications(unread: boolean, offset: number, limit = 20) {
  return useQuery({
    queryKey: okeys.notifications(unread, offset),
    staleTime: 0,
    queryFn: () =>
      unwrap(
        browserClient().api.GET("/api/v1/notifications", {
          params: { query: { unread, limit, offset } },
        }),
      ),
  });
}

function invalidateNotifications(qc: ReturnType<typeof useQueryClient>) {
  void qc.invalidateQueries({ queryKey: ["notifications"] });
}

export function useMarkRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(
        browserClient().api.POST("/api/v1/notifications/{notification_id}/read", {
          params: { path: { notification_id: id } },
        }),
      ),
    onSuccess: () => invalidateNotifications(qc),
  });
}

export function useMarkAllRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(browserClient().api.POST("/api/v1/notifications/read-all")),
    onSuccess: () => invalidateNotifications(qc),
  });
}

export function useNotificationPrefs() {
  return useQuery({
    queryKey: okeys.prefs,
    queryFn: () => unwrap(browserClient().api.GET("/api/v1/notifications/preferences")),
  });
}

export function useSetPreference() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { kind: string; email: boolean | null }) =>
      unwrap(
        browserClient().api.PUT("/api/v1/notifications/preferences", {
          body: { email: { [v.kind]: v.email } },
        }),
      ),
    onSuccess: (data) => qc.setQueryData(okeys.prefs, data),
  });
}

export function useRegenerateRecoveryCodes() {
  const qc = useQueryClient();
  return useMutation({
    meta: { ownAuthError: true },
    mutationFn: (code: string) =>
      unwrap(browserClient().api.POST("/api/v1/auth/mfa/recovery-codes", { body: { code } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: okeys.mfa });
      invalidateNotifications(qc);
    },
  });
}

export function useOrgStewardship(orgId: string | null) {
  return useQuery({
    queryKey: okeys.orgStewardship(orgId ?? ""),
    enabled: Boolean(orgId),
    queryFn: () =>
      unwrap(
        browserClient().api.GET("/api/v1/orgs/{org_id}/stewardship", {
          params: { path: { org_id: orgId ?? "" } },
        }),
      ),
  });
}

export function useOrgMembers(orgId: string | null) {
  return useQuery({
    queryKey: okeys.members(orgId ?? ""),
    enabled: Boolean(orgId),
    retry: false,
    queryFn: () =>
      unwrap(
        browserClient().api.GET("/api/v1/orgs/{org_id}/members", {
          params: { path: { org_id: orgId ?? "" } },
        }),
      ),
  });
}

export function useAddMember(orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { email: string; role: "admin" | "field_agent" | "member" }) =>
      unwrap(
        browserClient().api.POST("/api/v1/orgs/{org_id}/members", {
          params: { path: { org_id: orgId } },
          body,
        }),
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: okeys.members(orgId) }),
  });
}

async function loadSummary(project: ProjectSummary["project"]): Promise<ProjectSummary> {
  const c = browserClient();
  const id = project.id;
  const [classification, ndvi, alerts, periods] = await Promise.all([
    settle(
      unwrap(
        c.api.GET("/api/v1/projects/{project_id}/classifications", {
          params: { path: { project_id: id } },
        }),
      ).then(
        (rows) =>
          [...rows].sort(
            (a, b) =>
              b.season_start_year - a.season_start_year || b.created_at.localeCompare(a.created_at),
          )[0] ?? null,
      ),
    ),
    settle(
      c.analysis.timeseries(id, { index: "ndvi", min_clear: 0.5 }).then((t) => {
        const p = [...t.points]
          .filter((x) => x.p50 !== null)
          .sort((a, b) => b.date.localeCompare(a.date))[0];
        return p && p.p50 !== null ? { value: p.p50, date: p.date } : null;
      }),
    ),
    settle(c.analysis.alerts(id)),
    settle(c.field.listPeriods(id)),
  ]);
  const list = periods.ok ? periods.data : [];
  const reports = periods.ok
    ? await settle(
        Promise.all(
          list.map((p) =>
            unwrap(
              c.api.GET("/api/v1/periods/{period_id}/reports", {
                params: { path: { period_id: p.id } },
              }),
            ),
          ),
        ).then((r) => r.flat()),
      )
    : periods;
  const submissions = periods.ok
    ? await settle(
        Promise.all(
          list.map(async (period) => ({ period, items: await c.field.listSubmissions(period.id) })),
        ),
      )
    : periods;
  return {
    project,
    classification,
    ndvi,
    openAlerts: alerts.ok
      ? { ok: true, data: alerts.data.filter((a) => a.acknowledged_at === null) }
      : alerts,
    periods,
    reports,
    submissions,
  };
}

/** Everything the overview and community pages need about each project; each part fails alone. */
export function useProjectSummaries(projects: ProjectSummary["project"][]) {
  return useQueries({
    queries: projects.map((p) => ({
      queryKey: okeys.summary(p.id),
      queryFn: () => loadSummary(p),
    })),
  });
}

export function useMyOrgs() {
  return useQuery({
    queryKey: ["my-orgs"],
    queryFn: () => unwrap(browserClient().api.GET("/api/v1/orgs")),
  });
}
