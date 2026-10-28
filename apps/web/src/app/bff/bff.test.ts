// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GET as proxyGet } from "./api/[...path]/route";
import { POST as login } from "./auth/login/route";
import { POST as verifyMfa } from "./auth/mfa/route";

const API = "http://api.test";

function req(
  path: string,
  init: { method?: string; body?: unknown; cookies?: string; csrf?: boolean } = {},
) {
  const headers = new Headers({ "content-type": "application/json" });
  if (init.csrf !== false) headers.set("x-aquacreds-csrf", "1");
  if (init.cookies) headers.set("cookie", init.cookies);
  return new NextRequest(`http://web.test${path}`, {
    method: init.method ?? "POST",
    headers,
    ...(init.body === undefined ? {} : { body: JSON.stringify(init.body) }),
  });
}

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

type Cookie = { value: string; attrs: string[] };

function cookieMap(res: Response): Partial<Record<"aq_at" | "aq_rt" | "aq_mfa", Cookie>> {
  return Object.fromEntries(
    res.headers.getSetCookie().map((c) => {
      const [pair = "", ...attrs] = c.split(";");
      const [name = "", value = ""] = pair.split("=");
      return [name.trim(), { value, attrs: attrs.map((a) => a.trim().toLowerCase()) }];
    }),
  );
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.stubEnv("AQUA_API_URL", API);
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("/bff/auth/login", () => {
  it("stores tokens only in httpOnly cookies", async () => {
    fetchMock.mockResolvedValueOnce(
      json(200, { access_token: "AT", refresh_token: "RT", expires_in: 900 }),
    );
    const res = await login(
      req("/bff/auth/login", { body: { email: "a@example.org", password: "pw" } }),
    );
    expect(res.status).toBe(200);
    const body = await res.text();
    expect(body).not.toContain("AT");
    expect(body).not.toContain("RT");
    const c = cookieMap(res);
    expect(c.aq_at?.value).toBe("AT");
    expect(c.aq_at?.attrs).toContain("httponly");
    expect(c.aq_rt?.value).toBe("RT");
    expect(c.aq_rt?.attrs).toContain("samesite=lax");
  });

  it("keeps the MFA challenge server-side and asks for a code", async () => {
    fetchMock.mockResolvedValueOnce(
      json(200, { mfa_required: true, mfa_token: "CHALLENGE", expires_in: 300 }),
    );
    const res = await login(
      req("/bff/auth/login", { body: { email: "a@example.org", password: "pw" } }),
    );
    expect(await res.json()).toEqual({ mfa_required: true, expires_in: 300 });
    const c = cookieMap(res);
    expect(c.aq_mfa?.value).toBe("CHALLENGE");
    expect(c.aq_at?.value).toBe("");
  });

  it("passes the API's error through", async () => {
    fetchMock.mockResolvedValueOnce(json(401, { detail: "Invalid email or password" }));
    const res = await login(
      req("/bff/auth/login", { body: { email: "a@example.org", password: "bad" } }),
    );
    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ detail: "Invalid email or password" });
  });

  it("refuses requests without the CSRF header", async () => {
    const res = await login(req("/bff/auth/login", { body: {}, csrf: false }));
    expect(res.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("/bff/auth/mfa", () => {
  it("exchanges the challenge cookie and code for session cookies", async () => {
    fetchMock.mockResolvedValueOnce(
      json(200, { access_token: "AT2", refresh_token: "RT2", expires_in: 900 }),
    );
    const res = await verifyMfa(
      req("/bff/auth/mfa", { body: { code: "123456" }, cookies: "aq_mfa=CHALLENGE" }),
    );
    expect(res.status).toBe(200);
    const sent = fetchMock.mock.calls[0]?.[0] as Request;
    expect(await sent.json()).toEqual({ mfa_token: "CHALLENGE", code: "123456" });
    expect(cookieMap(res).aq_at?.value).toBe("AT2");
  });
});

describe("/bff/api proxy", () => {
  const ctx = (path: string[]) => ({ params: Promise.resolve({ path }) });

  it("adds the bearer token from the cookie", async () => {
    fetchMock.mockResolvedValueOnce(json(200, []));
    const res = await proxyGet(
      req("/bff/api/v1/projects", { method: "GET", cookies: "aq_at=AT" }),
      ctx(["v1", "projects"]),
    );
    expect(res.status).toBe(200);
    const sent = fetchMock.mock.calls[0]?.[0] as Request;
    expect(sent.url).toBe(`${API}/api/v1/projects`);
    expect(sent.headers.get("authorization")).toBe("Bearer AT");
  });

  it("refreshes once on 401 and rotates the cookies", async () => {
    fetchMock
      .mockResolvedValueOnce(json(401, { detail: "expired" }))
      .mockResolvedValueOnce(
        json(200, { access_token: "NEW", refresh_token: "RT-NEW", expires_in: 900 }),
      )
      .mockResolvedValueOnce(json(200, [{ id: "p1" }]));
    const res = await proxyGet(
      req("/bff/api/v1/projects", { method: "GET", cookies: "aq_at=OLD; aq_rt=RT-OLD" }),
      ctx(["v1", "projects"]),
    );
    expect(res.status).toBe(200);
    expect((fetchMock.mock.calls[2]![0] as Request).headers.get("authorization")).toBe(
      "Bearer NEW",
    );
    const c = cookieMap(res);
    expect(c.aq_at?.value).toBe("NEW");
    expect(c.aq_rt?.value).toBe("RT-NEW");
  });

  it("does not proxy token endpoints", async () => {
    const res = await proxyGet(
      req("/bff/api/v1/auth/refresh", { method: "GET" }),
      ctx(["v1", "auth", "refresh"]),
    );
    expect(res.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
