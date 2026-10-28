"use client";

import { ApiError, type AquaClient, createAquaClient } from "@aquacreds/api-client";
import { CSRF_HEADER } from "./csrf";

/** Fetch through the same-origin BFF; adds the CSRF header to state-changing requests. */
export function bffFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = new Request(input, init);
  if (request.method !== "GET" && request.method !== "HEAD") request.headers.set(CSRF_HEADER, "1");
  return fetch(request, { credentials: "same-origin" });
}

let client: AquaClient | null = null;

/** Typed API client for the browser. It holds no tokens: the BFF adds them from httpOnly cookies. */
export function browserClient(): AquaClient {
  client ??= createAquaClient({ baseUrl: `${window.location.origin}/bff`, fetch: bffFetch });
  return client;
}

/** POST JSON to a BFF auth/demo handler; throws ApiError with the API's `detail` on failure. */
export async function postBff<T>(path: string, body?: unknown): Promise<T> {
  const res = await bffFetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, data);
  return data as T;
}

/** Human message for an API error (FastAPI `detail` can be a string or validation errors). */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (typeof err.detail === "string") return err.detail;
    if (Array.isArray(err.detail)) {
      return err.detail
        .map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : String(d)))
        .join("; ");
    }
    return `Request failed (${err.status})`;
  }
  return err instanceof Error ? err.message : "Something went wrong";
}
