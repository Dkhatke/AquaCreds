import { describe, expect, it, vi } from "vitest";
import { ApiError, createAquaClient, memoryTokenStore, type TokenSet } from "../src";
import { type Call, json, mockFetch, tick } from "./mock-fetch";

const BASE = "http://api.test";
const user = {
  id: "00000000-0000-0000-0000-000000000001",
  email: "a@b.in",
  full_name: "A",
  platform_role: "user",
  created_at: "2026-10-01T00:00:00Z",
  memberships: [],
};
const pair = (n: number) => ({
  access_token: `A${n}`,
  refresh_token: `R${n}`,
  token_type: "bearer",
  expires_in: 900,
});
const bearer = (c: Call) => c.headers.get("Authorization");

function setup(routes: Parameters<typeof mockFetch>[0], initial: TokenSet | null = null) {
  const mock = mockFetch(routes);
  const store = memoryTokenStore(initial);
  const onLogout = vi.fn();
  const client = createAquaClient({
    baseUrl: `${BASE}/`,
    fetch: mock.fetch,
    tokenStore: store,
    onLogout,
  });
  return { ...mock, store, onLogout, client };
}

/** /auth/me accepts only the given access token. */
const meAccepting = (token: string) => (c: Call) =>
  bearer(c) === `Bearer ${token}` ? json(200, user) : json(401, { detail: "Invalid token" });

describe("token injection", () => {
  it("sends the stored access token as a bearer header", async () => {
    const t = setup(
      { "GET /api/v1/auth/me": meAccepting("A1") },
      { accessToken: "A1", refreshToken: "R1" },
    );
    await expect(t.client.auth.me()).resolves.toEqual(user);
    expect(bearer(t.calls[0]!)).toBe("Bearer A1");
    expect(t.calls[0]!.url.href).toBe(`${BASE}/api/v1/auth/me`);
  });

  it("login stores the pair and later calls use it; login itself carries no bearer", async () => {
    const t = setup({
      "POST /api/v1/auth/login": () => json(200, pair(1)),
      "GET /api/v1/auth/me": meAccepting("A1"),
    });
    await t.client.auth.login("a@b.in", "correct horse");
    expect(t.callsTo("POST /api/v1/auth/login")[0]!.headers.has("Authorization")).toBe(false);
    expect(JSON.parse(t.callsTo("POST /api/v1/auth/login")[0]!.body)).toEqual({
      email: "a@b.in",
      password: "correct horse",
    });
    expect(await t.store.get()).toEqual({ accessToken: "A1", refreshToken: "R1" });
    await t.client.auth.me();
    expect(bearer(t.callsTo("GET /api/v1/auth/me")[0]!)).toBe("Bearer A1");
  });

  it("login with two-factor returns the challenge, stores nothing, then verifyMfa stores the pair", async () => {
    const challenge = { mfa_required: true, mfa_token: "M1", expires_in: 300 };
    const t = setup({
      "POST /api/v1/auth/login": () => json(200, challenge),
      "POST /api/v1/auth/mfa/verify": () => json(200, pair(1)),
    });
    await expect(t.client.auth.login("a@b.in", "correct horse")).resolves.toEqual(challenge);
    expect(await t.store.get()).toBeNull();
    await t.client.auth.verifyMfa("M1", "123456");
    expect(JSON.parse(t.callsTo("POST /api/v1/auth/mfa/verify")[0]!.body)).toEqual({
      mfa_token: "M1",
      code: "123456",
    });
    expect(await t.store.get()).toEqual({ accessToken: "A1", refreshToken: "R1" });
  });

  it("a rejected MFA code is an error, not a refresh or logout", async () => {
    const t = setup(
      { "POST /api/v1/auth/mfa/verify": () => json(401, { detail: "Invalid code" }) },
      { accessToken: "A0", refreshToken: "R0" },
    );
    await expect(t.client.auth.verifyMfa("M1", "000000")).rejects.toMatchObject({ status: 401 });
    expect(t.callsTo("POST /api/v1/auth/refresh")).toHaveLength(0);
    expect(t.onLogout).not.toHaveBeenCalled();
  });

  it("anonymous 401 is returned as an error without refresh or logout", async () => {
    const t = setup({ "GET /api/v1/auth/me": () => json(401, { detail: "Not authenticated" }) });
    await expect(t.client.auth.me()).rejects.toMatchObject({
      status: 401,
      detail: "Not authenticated",
    });
    expect(t.callsTo("POST /api/v1/auth/refresh")).toHaveLength(0);
    expect(t.onLogout).not.toHaveBeenCalled();
  });
});

describe("automatic refresh", () => {
  it("concurrent 401s trigger exactly one refresh, then every request is retried", async () => {
    const t = setup(
      {
        "GET /api/v1/auth/me": meAccepting("A2"),
        "POST /api/v1/auth/refresh": async () => {
          await tick(20);
          return json(200, pair(2));
        },
      },
      { accessToken: "A1", refreshToken: "R1" },
    );
    const results = await Promise.all(Array.from({ length: 5 }, () => t.client.auth.me()));
    expect(results).toEqual(Array(5).fill(user));
    const refreshes = t.callsTo("POST /api/v1/auth/refresh");
    expect(refreshes).toHaveLength(1);
    expect(JSON.parse(refreshes[0]!.body)).toEqual({ refresh_token: "R1" });
    expect(await t.store.get()).toEqual({ accessToken: "A2", refreshToken: "R2" });
    // 5 rejected with A1 + 5 retried with A2
    expect(t.callsTo("GET /api/v1/auth/me").map(bearer).sort()).toEqual([
      ...Array(5).fill("Bearer A1"),
      ...Array(5).fill("Bearer A2"),
    ]);
  });

  it("a 401 arriving after another request already refreshed reuses the new token", async () => {
    let slowFirst = true;
    const t = setup(
      {
        "GET /api/v1/auth/me": async (c) => {
          if (slowFirst && bearer(c) === "Bearer A1") {
            slowFirst = false;
            await tick(40); // responds 401 after the other request has refreshed
          }
          return meAccepting("A2")(c);
        },
        "POST /api/v1/auth/refresh": () => json(200, pair(2)),
      },
      { accessToken: "A1", refreshToken: "R1" },
    );
    const slow = t.client.auth.me();
    await tick(5);
    await t.client.auth.me();
    await slow;
    expect(t.callsTo("POST /api/v1/auth/refresh")).toHaveLength(1);
  });

  it("replays the original request body on retry", async () => {
    const created = { id: "p1" };
    const t = setup(
      {
        "POST /api/v1/projects": (c) =>
          bearer(c) === "Bearer A2" ? json(201, created) : json(401, { detail: "expired" }),
        "POST /api/v1/auth/refresh": () => json(200, pair(2)),
      },
      { accessToken: "A1", refreshToken: "R1" },
    );
    const body = { name: "Sundarbans block 4" } as unknown as Parameters<
      typeof t.client.projects.create
    >[0];
    await expect(t.client.projects.create(body)).resolves.toEqual(created);
    const posts = t.callsTo("POST /api/v1/projects");
    expect(posts).toHaveLength(2);
    expect(posts[1]!.body).toBe(posts[0]!.body);
    expect(JSON.parse(posts[1]!.body)).toEqual(body);
  });

  it("refresh rejection clears tokens and calls onLogout once, even for concurrent requests", async () => {
    const t = setup(
      {
        "GET /api/v1/auth/me": meAccepting("never"),
        "POST /api/v1/auth/refresh": async () => {
          await tick(10);
          return json(401, { detail: "Invalid refresh token" });
        },
      },
      { accessToken: "A1", refreshToken: "R1" },
    );
    const results = await Promise.allSettled([
      t.client.auth.me(),
      t.client.auth.me(),
      t.client.auth.me(),
    ]);
    for (const r of results) {
      expect(r.status).toBe("rejected");
      expect((r as PromiseRejectedResult).reason).toBeInstanceOf(ApiError);
      expect((r as PromiseRejectedResult).reason.status).toBe(401);
    }
    expect(t.callsTo("POST /api/v1/auth/refresh")).toHaveLength(1);
    expect(t.onLogout).toHaveBeenCalledTimes(1);
    expect(t.onLogout).toHaveBeenCalledWith("refresh_failed");
    expect(await t.store.get()).toBeNull();
  });

  it("a 5xx during refresh keeps the session (no logout) and surfaces the error", async () => {
    const t = setup(
      {
        "GET /api/v1/auth/me": meAccepting("A2"),
        "POST /api/v1/auth/refresh": () => json(503, { detail: "db down" }),
      },
      { accessToken: "A1", refreshToken: "R1" },
    );
    await expect(t.client.auth.me()).rejects.toMatchObject({ status: 503 });
    expect(t.onLogout).not.toHaveBeenCalled();
    expect(await t.store.get()).toEqual({ accessToken: "A1", refreshToken: "R1" });
  });

  it("explicit logout revokes the refresh token and clears the store", async () => {
    const t = setup(
      { "POST /api/v1/auth/logout": () => new Response(null, { status: 204 }) },
      { accessToken: "A1", refreshToken: "R1" },
    );
    await t.client.auth.logout();
    expect(JSON.parse(t.callsTo("POST /api/v1/auth/logout")[0]!.body)).toEqual({
      refresh_token: "R1",
    });
    expect(t.onLogout).toHaveBeenCalledWith("user");
    expect(await t.store.get()).toBeNull();
  });
});
