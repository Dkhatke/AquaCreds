export interface Call {
  method: string;
  url: URL;
  headers: Headers;
  body: string;
}

type Handler = (call: Call) => Response | Promise<Response>;

export const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

/** A fetch double that records every call and routes `METHOD /path` to a handler. */
export function mockFetch(routes: Record<string, Handler>) {
  const calls: Call[] = [];
  const fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const req = new Request(input, init);
    const call: Call = {
      method: req.method,
      url: new URL(req.url),
      headers: req.headers,
      body: req.body ? await req.text() : "",
    };
    calls.push(call);
    const handler = routes[`${call.method} ${call.url.pathname}`];
    if (!handler) return json(404, { detail: `no route for ${call.method} ${call.url.pathname}` });
    return handler(call);
  };
  const callsTo = (key: string) => calls.filter((c) => `${c.method} ${c.url.pathname}` === key);
  return { fetch: fetch as typeof globalThis.fetch, calls, callsTo };
}

export const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));
