"use client";

import { type Schema, unwrap } from "@aquacreds/api-client";
import { useQuery } from "@tanstack/react-query";
import { bffFetch, browserClient } from "./api-browser";

export interface ListingFilters {
  credit_type?: "temporary" | "long_term" | undefined;
  state?: string | undefined;
  max_price?: number | undefined;
  sort: "newest" | "price_asc" | "price_desc";
  include_unavailable: boolean;
  project_id?: string | undefined;
}

export const DEFAULT_FILTERS: ListingFilters = {
  sort: "newest",
  include_unavailable: false,
};

/** Drop undefined keys so the generated query type (exactOptionalPropertyTypes) is satisfied. */
function compact<T extends object>(o: T): { [K in keyof T]?: Exclude<T[K], undefined> } {
  return Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined)) as {
    [K in keyof T]?: Exclude<T[K], undefined>;
  };
}

export const rk = {
  session: ["session"] as const,
  me: ["me"] as const,
  listings: (f: ListingFilters) => ["listings", f] as const,
  market: (id: string) => ["market", id] as const,
  trades: (id: string | null) => ["trades", id] as const,
  chainStatus: ["chain-status"] as const,
  projectChain: (id: string) => ["project-chain", id] as const,
  reports: (id: string) => ["project-reports", id] as const,
  vintages: (id: string | null) => ["vintages", id] as const,
  retirements: ["retirements"] as const,
  ffRequests: ["ff-requests"] as const,
  ffRequest: (id: number) => ["ff-request", id] as const,
  ffProject: (id: string) => ["ff-project", id] as const,
};

export interface BffSession {
  authenticated: boolean;
  demo: boolean;
  banner: string | null;
}

/** Whether the session is a read-only demo (the BFF knows from its httpOnly cookie). */
export function useSession() {
  return useQuery({
    queryKey: rk.session,
    queryFn: async (): Promise<BffSession> => {
      const res = await bffFetch("/bff/session");
      if (!res.ok) throw new Error(`Session check failed (HTTP ${res.status})`);
      return (await res.json()) as BffSession;
    },
  });
}

export function useMe() {
  return useQuery({ queryKey: rk.me, queryFn: () => browserClient().auth.me() });
}

export function useListings(filters: ListingFilters) {
  return useQuery({
    queryKey: rk.listings(filters),
    queryFn: () =>
      unwrap(
        browserClient().api.GET("/api/v1/public/marketplace/listings", {
          params: { query: compact({ ...filters, limit: 100 }) },
        }),
      ),
  });
}

export function useProjectMarket(projectId: string | null) {
  return useQuery({
    queryKey: rk.market(projectId ?? ""),
    enabled: Boolean(projectId),
    queryFn: () =>
      unwrap(
        browserClient().api.GET("/api/v1/public/marketplace/projects/{project_id}/market", {
          params: { path: { project_id: projectId ?? "" } },
        }),
      ),
  });
}

export function useTrades(projectId: string | null) {
  return useQuery({
    queryKey: rk.trades(projectId),
    queryFn: () =>
      unwrap(
        browserClient().api.GET("/api/v1/public/marketplace/trades", {
          params: { query: compact({ project_id: projectId ?? undefined, limit: 100 }) },
        }),
      ),
  });
}

export function useChainStatus() {
  return useQuery({
    queryKey: rk.chainStatus,
    queryFn: () => browserClient().chain.status(),
    refetchInterval: 30_000,
  });
}

export function useProjectChain(projectId: string | null) {
  return useQuery({
    queryKey: rk.projectChain(projectId ?? ""),
    enabled: Boolean(projectId),
    queryFn: () => browserClient().chain.project(projectId ?? ""),
    refetchInterval: 15_000,
  });
}

/** Every report of every monitoring period of a project, newest first. */
export function useProjectReports(projectId: string | null) {
  return useQuery({
    queryKey: rk.reports(projectId ?? ""),
    enabled: Boolean(projectId),
    queryFn: async (): Promise<Schema<"ReportOut">[]> => {
      const client = browserClient();
      const periods = await client.field.listPeriods(projectId ?? "");
      const lists = await Promise.all(
        periods.map((p) =>
          unwrap(
            client.api.GET("/api/v1/periods/{period_id}/reports", {
              params: { path: { period_id: p.id } },
            }),
          ),
        ),
      );
      return lists.flat().sort((a, b) => b.generated_at.localeCompare(a.generated_at));
    },
  });
}

export function useVintages(projectId: string | null) {
  return useQuery({
    queryKey: rk.vintages(projectId),
    queryFn: () =>
      unwrap(
        browserClient().api.GET("/api/v1/public/vintages", {
          params: { query: compact({ project_id: projectId ?? undefined, limit: 100 }) },
        }),
      ),
  });
}

export function useRetirements() {
  return useQuery({
    queryKey: rk.retirements,
    queryFn: () =>
      unwrap(
        browserClient().api.GET("/api/v1/public/retirements", {
          params: { query: { limit: 100 } },
        }),
      ),
  });
}

export function useForwardRequests() {
  return useQuery({
    queryKey: rk.ffRequests,
    queryFn: () =>
      unwrap(browserClient().api.GET("/api/v1/forward-fund/requests", { params: { query: {} } })),
  });
}

export function useForwardRequest(requestId: number | null) {
  return useQuery({
    queryKey: rk.ffRequest(requestId ?? -1),
    enabled: requestId !== null,
    queryFn: () =>
      unwrap(
        browserClient().api.GET("/api/v1/forward-fund/requests/{request_id}", {
          params: { path: { request_id: requestId ?? 0 }, query: {} },
        }),
      ),
  });
}

export function useProjectForwardFund(projectId: string | null) {
  return useQuery({
    queryKey: rk.ffProject(projectId ?? ""),
    enabled: Boolean(projectId),
    queryFn: () =>
      unwrap(
        browserClient().api.GET("/api/v1/projects/{project_id}/forward-fund", {
          params: { path: { project_id: projectId ?? "" } },
        }),
      ),
  });
}
