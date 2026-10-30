"use client";

import { unwrap } from "@aquacreds/api-client";
import { useQueries, useQuery } from "@tanstack/react-query";
import { bffFetch, browserClient } from "./api-browser";
import { TileJson } from "./pending-contracts";

export type SeriesIndex = "ndvi" | "mvi" | "vh_db";

export const keys = {
  projects: ["projects"] as const,
  project: (id: string) => ["project", id] as const,
  classifications: (id: string) => ["classifications", id] as const,
  timeseries: (id: string, index: SeriesIndex, minClear: number) =>
    ["timeseries", id, index, minClear] as const,
  alerts: (id: string) => ["alerts", id] as const,
  stewardship: (id: string) => ["stewardship", id] as const,
  tilejson: (id: string, cid: string) => ["tilejson", id, cid] as const,
  me: ["me"] as const,
  periods: (id: string) => ["periods", id] as const,
  reports: (periodId: string) => ["reports", periodId] as const,
  report: (id: string) => ["report", id] as const,
  verify: (id: string) => ["report-verify", id] as const,
  publicReport: (id: string) => ["public-report", id] as const,
  plots: (id: string) => ["plots", id] as const,
  submissions: (periodId: string) => ["submissions", periodId] as const,
  projectChain: (id: string) => ["project-chain", id] as const,
};

export function useProjects() {
  return useQuery({
    queryKey: keys.projects,
    queryFn: () => browserClient().projects.list({ limit: 200 }),
  });
}

export function useProject(id: string) {
  return useQuery({ queryKey: keys.project(id), queryFn: () => browserClient().projects.get(id) });
}

export function useClassifications(id: string) {
  return useQuery({
    queryKey: keys.classifications(id),
    queryFn: () =>
      unwrap(
        browserClient().api.GET("/api/v1/projects/{project_id}/classifications", {
          params: { path: { project_id: id } },
        }),
      ),
  });
}

export function useTimeseries(id: string, index: SeriesIndex, minClear: number) {
  return useQuery({
    queryKey: keys.timeseries(id, index, minClear),
    queryFn: () => browserClient().analysis.timeseries(id, { index, min_clear: minClear }),
  });
}

export function useAlerts(id: string) {
  return useQuery({
    queryKey: keys.alerts(id),
    queryFn: () => browserClient().analysis.alerts(id),
  });
}

export function useStewardship(id: string) {
  return useQuery({
    queryKey: keys.stewardship(id),
    queryFn: () =>
      unwrap(
        browserClient().api.GET("/api/v1/projects/{project_id}/stewardship", {
          params: { path: { project_id: id } },
        }),
      ),
  });
}

/** Classification tiles (pending API contract; only requested when FEATURE_TILES is on). */
export function useTileJson(id: string, cid: string | null, enabled: boolean) {
  return useQuery({
    queryKey: keys.tilejson(id, cid ?? ""),
    enabled: enabled && Boolean(cid),
    retry: false,
    queryFn: async () => {
      const qs = new URLSearchParams({ layer: "classification", cid: cid ?? "" });
      const res = await bffFetch(`/bff/api/v1/projects/${encodeURIComponent(id)}/tilejson?${qs}`);
      if (!res.ok) throw new Error(`Tiles unavailable (HTTP ${res.status})`);
      const parsed = TileJson.safeParse(await res.json());
      if (!parsed.success) throw new Error("Unexpected TileJSON from the API");
      return parsed.data;
    },
  });
}

export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: () => unwrap(browserClient().api.GET("/api/v1/auth/me")),
  });
}

export function usePeriods(projectId: string) {
  return useQuery({
    queryKey: keys.periods(projectId),
    queryFn: () =>
      unwrap(
        browserClient().api.GET("/api/v1/projects/{project_id}/periods", {
          params: { path: { project_id: projectId } },
        }),
      ),
    // A report being built flips the period to `analysing`; poll until it is under review.
    refetchInterval: (q) => (q.state.data?.some((p) => p.status === "analysing") ? 4000 : false),
  });
}

/** Report versions of several periods at once (one request each). */
export function useReportsForPeriods(periodIds: string[]) {
  return useQueries({
    queries: periodIds.map((periodId) => ({
      queryKey: keys.reports(periodId),
      queryFn: () =>
        unwrap(
          browserClient().api.GET("/api/v1/periods/{period_id}/reports", {
            params: { path: { period_id: periodId } },
          }),
        ),
    })),
  });
}

export function useReport(reportId: string) {
  return useQuery({
    queryKey: keys.report(reportId),
    queryFn: () =>
      unwrap(
        browserClient().api.GET("/api/v1/reports/{report_id}", {
          params: { path: { report_id: reportId } },
        }),
      ),
  });
}

/** DB vs stored vs on-chain hash comparison; only runs when asked (it streams the stored file). */
export function useVerifyReport(reportId: string, enabled: boolean) {
  return useQuery({
    queryKey: keys.verify(reportId),
    enabled,
    staleTime: 0,
    retry: false,
    queryFn: () =>
      unwrap(
        browserClient().api.GET("/api/v1/reports/{report_id}/verify", {
          params: { path: { report_id: reportId } },
        }),
      ),
  });
}

/** Methodology profile and software versions (approved reports of public projects only). */
export function usePublicReport(reportId: string, enabled: boolean) {
  return useQuery({
    queryKey: keys.publicReport(reportId),
    enabled,
    retry: false,
    queryFn: () =>
      unwrap(
        browserClient().api.GET("/api/v1/public/reports/{report_id}", {
          params: { path: { report_id: reportId } },
        }),
      ),
  });
}

export function usePlots(projectId: string) {
  return useQuery({
    queryKey: keys.plots(projectId),
    queryFn: () =>
      unwrap(
        browserClient().api.GET("/api/v1/projects/{project_id}/plots", {
          params: { path: { project_id: projectId } },
        }),
      ),
  });
}

export function useSubmissionsForPeriods(periodIds: string[]) {
  return useQueries({
    queries: periodIds.map((periodId) => ({
      queryKey: keys.submissions(periodId),
      queryFn: () =>
        unwrap(
          browserClient().api.GET("/api/v1/periods/{period_id}/submissions", {
            params: { path: { period_id: periodId } },
          }),
        ),
    })),
  });
}

export function useProjectChain(projectId: string, enabled: boolean) {
  return useQuery({
    queryKey: keys.projectChain(projectId),
    enabled,
    queryFn: () =>
      unwrap(
        browserClient().api.GET("/api/v1/projects/{project_id}/chain", {
          params: { path: { project_id: projectId } },
        }),
      ),
  });
}

export function useChainTx(txId: string | null) {
  return useQuery({
    queryKey: ["chain-tx", txId] as const,
    enabled: txId !== null,
    queryFn: () =>
      unwrap(
        browserClient().api.GET("/api/v1/chain/transactions/{tx_id}", {
          params: { path: { tx_id: txId ?? "" } },
        }),
      ),
  });
}
