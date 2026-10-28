/**
 * Field reference data (projects with boundaries, editable periods, sample plots) fetched from the
 * API and cached in SQLite so the capture and measurement screens work without signal.
 */
import type { AquaClient, ProjectOut } from "@aquacreds/api-client";
import { unwrap } from "@aquacreds/api-client";
import type { CacheStore } from "../outbox/store";
import type { PeriodOut, PlotOut } from "./AppProvider";

/** The API accepts evidence while a period is open or sent back by a verifier. */
export const EDITABLE_PERIOD = new Set(["open", "changes_requested"]);

export interface ProjectWithPeriods {
  project: Pick<ProjectOut, "id" | "name" | "boundary" | "state">;
  periods: PeriodOut[];
}

export interface Cached<T> {
  value: T;
  /** Epoch ms of the cached copy, or null when fresh from the network. */
  cachedAt: number | null;
}

const PROJECTS_KEY = "field.projects";
const plotsKey = (projectId: string) => `field.plots.${projectId}`;

export async function loadProjects(
  client: AquaClient,
  cache: CacheStore,
  online: boolean,
): Promise<Cached<ProjectWithPeriods[]>> {
  if (online) {
    try {
      const projects = await client.projects.list({ limit: 200 });
      const withPeriods = await Promise.all(
        projects.map(async (p) => ({
          project: { id: p.id, name: p.name, boundary: p.boundary, state: p.state },
          periods: (await client.field.listPeriods(p.id)).filter((q) =>
            EDITABLE_PERIOD.has(q.status),
          ),
        })),
      );
      await cache.set(PROJECTS_KEY, withPeriods);
      return { value: withPeriods, cachedAt: null };
    } catch (err) {
      const cached = await cache.get<ProjectWithPeriods[]>(PROJECTS_KEY);
      if (cached) return { value: cached.value, cachedAt: cached.updatedAt };
      throw err;
    }
  }
  const cached = await cache.get<ProjectWithPeriods[]>(PROJECTS_KEY);
  return { value: cached?.value ?? [], cachedAt: cached?.updatedAt ?? null };
}

export async function loadPlots(
  client: AquaClient,
  cache: CacheStore,
  projectId: string,
  online: boolean,
): Promise<Cached<PlotOut[]>> {
  if (online) {
    try {
      const plots = await unwrap(
        client.api.GET("/api/v1/projects/{project_id}/plots", {
          params: { path: { project_id: projectId } },
        }),
      );
      await cache.set(plotsKey(projectId), plots);
      return { value: plots, cachedAt: null };
    } catch (err) {
      const cached = await cache.get<PlotOut[]>(plotsKey(projectId));
      if (cached) return { value: cached.value, cachedAt: cached.updatedAt };
      throw err;
    }
  }
  const cached = await cache.get<PlotOut[]>(plotsKey(projectId));
  return { value: cached?.value ?? [], cachedAt: cached?.updatedAt ?? null };
}
