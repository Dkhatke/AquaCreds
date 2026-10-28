import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import type { SqlDb, SqlValue } from "../src/outbox/store";

/** expo-sqlite's async API over Node's built-in SQLite, so the outbox SQL runs for real. */
export function nodeSqlDb(): SqlDb & { raw: DatabaseSync } {
  const raw = new DatabaseSync(":memory:");
  const args = (params: SqlValue[]) => params as SQLInputValue[];
  return {
    raw,
    async execAsync(sql) {
      raw.exec(sql);
    },
    async runAsync(sql, params) {
      return raw.prepare(sql).run(...args(params));
    },
    async getAllAsync<T>(sql: string, params: SqlValue[]) {
      return raw.prepare(sql).all(...args(params)) as T[];
    },
    async getFirstAsync<T>(sql: string, params: SqlValue[]) {
      return (raw.prepare(sql).get(...args(params)) as T | undefined) ?? null;
    },
  };
}

export interface Call {
  method: string;
  url: URL;
  headers: Headers;
  body: Uint8Array;
}

type Handler = (call: Call) => Response | Promise<Response>;

export const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });

/** A fetch double that records calls (with raw body bytes) and routes `METHOD host/path`. */
export function mockFetch(routes: Record<string, Handler>) {
  const calls: Call[] = [];
  const fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const req = new Request(input, init);
    const call: Call = {
      method: req.method,
      url: new URL(req.url),
      headers: req.headers,
      body: new Uint8Array(await req.arrayBuffer()),
    };
    calls.push(call);
    const handler = routes[`${call.method} ${call.url.pathname}`];
    if (!handler) return json(404, { detail: `no route for ${call.method} ${call.url.pathname}` });
    return handler(call);
  };
  const callsTo = (key: string) => calls.filter((c) => `${c.method} ${c.url.pathname}` === key);
  return { fetch: fetch as typeof globalThis.fetch, calls, callsTo };
}

let counter = 0;
export const seqId = () => `id-${String(++counter).padStart(4, "0")}`;
