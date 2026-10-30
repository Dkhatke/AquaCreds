import "server-only";
import { ApiError, createAquaClient, unwrap } from "@aquacreds/api-client";
import { apiOrigin } from "./config";

export type Loaded<T> = { ok: true; data: T } | { ok: false; status: number; message: string };

/** Anonymous client for the no-login `/api/v1/public/*` endpoints; revalidated every minute. */
function publicClient() {
  const origin = apiOrigin();
  if (!origin) return null;
  return createAquaClient({
    baseUrl: origin,
    fetch: (input, init) =>
      fetch(input, { ...init, next: { revalidate: 60 }, signal: AbortSignal.timeout(8000) }),
  });
}

/** Run a public API call; failures become values so pages can show them (never fake data). */
export async function load<T>(
  call: (c: NonNullable<ReturnType<typeof publicClient>>) => Promise<T>,
): Promise<Loaded<T>> {
  const client = publicClient();
  if (!client)
    return { ok: false, status: 503, message: "The registry API is not configured for this site." };
  try {
    return { ok: true, data: await call(client) };
  } catch (err) {
    if (err instanceof ApiError) {
      const detail = typeof err.detail === "string" ? err.detail : null;
      return {
        ok: false,
        status: err.status,
        message: detail ?? `The registry API returned HTTP ${err.status}.`,
      };
    }
    return { ok: false, status: 502, message: "The registry API could not be reached." };
  }
}

export const PAGE_SIZE = 20;

export const publicApi = {
  projects: (q: {
    state?: string;
    ecosystem?: "mangrove" | "salt_marsh" | "seagrass";
    page: number;
  }) =>
    load((c) =>
      unwrap(
        c.api.GET("/api/v1/public/projects", {
          params: {
            query: {
              limit: PAGE_SIZE,
              offset: (q.page - 1) * PAGE_SIZE,
              ...(q.state ? { state: q.state } : {}),
              ...(q.ecosystem ? { ecosystem: q.ecosystem } : {}),
            },
          },
        }),
      ),
    ),
  project: (id: string) =>
    load((c) =>
      unwrap(
        c.api.GET("/api/v1/public/projects/{project_id}", { params: { path: { project_id: id } } }),
      ),
    ),
  vintages: (page: number) =>
    load((c) =>
      unwrap(
        c.api.GET("/api/v1/public/vintages", {
          params: { query: { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE } },
        }),
      ),
    ),
  retirements: (page: number) =>
    load((c) =>
      unwrap(
        c.api.GET("/api/v1/public/retirements", {
          params: { query: { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE } },
        }),
      ),
    ),
  retirement: (id: number) =>
    load((c) =>
      unwrap(
        c.api.GET("/api/v1/public/retirements/{retirement_id}", {
          params: { path: { retirement_id: id } },
        }),
      ),
    ),
};
