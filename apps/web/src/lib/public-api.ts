import "server-only";
import { createAquaClient } from "@aquacreds/api-client";
import { apiOrigin } from "./config";

export interface PublicCounts {
  projects: number;
  vintages: number;
  retirements: number;
}

/**
 * Live registry counts from the no-login `/api/v1/public/*` endpoints. Returns null when the API
 * is not configured or unreachable: the landing page then hides the block instead of guessing.
 */
export async function getPublicCounts(): Promise<PublicCounts | null> {
  const origin = apiOrigin();
  if (!origin) return null;
  const client = createAquaClient({
    baseUrl: origin,
    fetch: (input, init) =>
      fetch(input, { ...init, next: { revalidate: 300 }, signal: AbortSignal.timeout(2500) }),
  });
  const page = { params: { query: { limit: 1, offset: 0 } } };
  try {
    const [p, v, r] = await Promise.all([
      client.api.GET("/api/v1/public/projects", page),
      client.api.GET("/api/v1/public/vintages", page),
      client.api.GET("/api/v1/public/retirements", page),
    ]);
    if (!p.data || !v.data || !r.data) return null;
    return { projects: p.data.total, vintages: v.data.total, retirements: r.data.total };
  } catch {
    return null;
  }
}
