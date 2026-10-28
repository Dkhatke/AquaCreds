"use client";

import { unwrap } from "@aquacreds/api-client";
import { useQuery } from "@tanstack/react-query";
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
