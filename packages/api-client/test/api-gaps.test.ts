import { describe, expect, expectTypeOf, it } from "vitest";
import { type components, createAquaClient, memoryTokenStore } from "../src";
import { json, mockFetch } from "./mock-fetch";

type S = components["schemas"];
const PROJECT = "22222222-2222-2222-2222-222222222222";
const PERIOD = "11111111-1111-1111-1111-111111111111";
const PLOT = "33333333-3333-3333-3333-333333333333";
const KEY = "44444444-4444-4444-4444-444444444444";

function client(routes: Parameters<typeof mockFetch>[0]) {
  const mock = mockFetch(routes);
  const tokenStore = memoryTokenStore({ accessToken: "at", refreshToken: "rt" });
  return {
    mock,
    c: createAquaClient({ baseUrl: "http://api.test", fetch: mock.fetch, tokenStore }),
  };
}

describe("API-gap helpers", () => {
  it("public stats and model cards", async () => {
    const stats = { projects: 3, area_ha: 1234.5 };
    const card = { version: "ecosystem_classifier_v1.1", heldout_metrics: { macro_f1: 0.6378 } };
    const { mock, c } = client({
      "GET /api/v1/public/stats": () => json(200, stats),
      "GET /api/v1/models": () => json(200, []),
      "GET /api/v1/models/ecosystem-classifier/ecosystem_classifier_v1.1": () => json(200, card),
    });
    expect(await c.public.stats()).toEqual(stats);
    expect(await c.models.list()).toEqual([]);
    expect(await c.models.version("ecosystem-classifier", "ecosystem_classifier_v1.1")).toEqual(
      card,
    );
    expect(mock.calls.map((x) => x.url.pathname)).toEqual([
      "/api/v1/public/stats",
      "/api/v1/models",
      "/api/v1/models/ecosystem-classifier/ecosystem_classifier_v1.1",
    ]);
  });

  it("evidence points pass precision and period as query", async () => {
    const { mock, c } = client({
      [`GET /api/v1/projects/${PROJECT}/evidence-points`]: () =>
        json(200, { type: "FeatureCollection", features: [] }),
    });
    await c.field.evidencePoints(PROJECT, { precision: "raw", period_id: PERIOD });
    const url = mock.calls[0]?.url;
    expect(url?.searchParams.get("precision")).toBe("raw");
    expect(url?.searchParams.get("period_id")).toBe(PERIOD);
  });

  it("issues a nonce batch with the requested count", async () => {
    const { mock, c } = client({
      [`POST /api/v1/periods/${PERIOD}/nonces/batch`]: () =>
        json(201, { mode: "batch", nonces: [] }),
    });
    await c.field.issueNonceBatch(PERIOD, 12);
    expect(JSON.parse(mock.calls[0]?.body ?? "{}")).toEqual({ count: 12 });
  });

  it("sends Idempotency-Key only when given", async () => {
    const path = `/api/v1/periods/${PERIOD}/plots/${PLOT}/measurements`;
    const { mock, c } = client({ [`POST ${path}`]: () => json(201, { id: "m1" }) });
    const body = { measured_on: "2026-11-02", trees: [], notes: "" };
    await c.field.recordMeasurement(PERIOD, PLOT, body, { idempotencyKey: KEY });
    await c.field.recordMeasurement(PERIOD, PLOT, body);
    expect(mock.calls[0]?.headers.get("Idempotency-Key")).toBe(KEY);
    expect(mock.calls[1]?.headers.get("Idempotency-Key")).toBeNull();
    expect(mock.calls[0]?.headers.get("Authorization")).toBe("Bearer at");
  });

  it("lists measurements and paginated submissions", async () => {
    const { mock, c } = client({
      [`GET /api/v1/periods/${PERIOD}/measurements`]: () => json(200, []),
      [`GET /api/v1/periods/${PERIOD}/submissions`]: () => json(200, []),
    });
    await c.field.listMeasurements(PERIOD, { plot_id: PLOT });
    await c.field.listSubmissions(PERIOD, { sha256: "a".repeat(64), limit: 50 });
    expect(mock.calls[0]?.url.searchParams.get("plot_id")).toBe(PLOT);
    expect(mock.calls[1]?.url.searchParams.get("sha256")).toBe("a".repeat(64));
    expect(mock.calls[1]?.url.searchParams.get("limit")).toBe("50");
  });

  it("helpers are typed from the schema", () => {
    const { c } = client({});
    expectTypeOf(c.public.stats).returns.resolves.toEqualTypeOf<S["PublicStats"]>();
    expectTypeOf(c.field.evidencePoints)
      .returns.resolves.toHaveProperty("precision")
      .toEqualTypeOf<"snapped" | "raw">();
    expectTypeOf(c.field.issueNonceBatch).returns.resolves.toEqualTypeOf<S["NonceBatchOut"]>();
    expectTypeOf(c.field.listMeasurements).returns.resolves.toEqualTypeOf<S["MeasurementOut"][]>();
    expectTypeOf(c.models.version).returns.resolves.toEqualTypeOf<S["ModelCardSummary"]>();
    expectTypeOf<S["ClassificationOut"]["model_card"]>().toEqualTypeOf<
      S["ModelCardSummary"] | null | undefined
    >();
  });
});
