import { describe, expectTypeOf, it } from "vitest";
import { type components, createAquaClient, type paths } from "../src";

// Type-level checks only: a stub fetch guarantees nothing ever reaches the network.
const offlineFetch: typeof fetch = async () => new Response(null, { status: 503 });
const client = createAquaClient({ baseUrl: "http://api.test", fetch: offlineFetch });

describe("generated types", () => {
  it("helpers return the schema's response types", () => {
    expectTypeOf(client.auth.me).returns.resolves.toEqualTypeOf<components["schemas"]["UserOut"]>();
    expectTypeOf(client.auth.login).returns.resolves.toEqualTypeOf<
      components["schemas"]["TokenPair"] | components["schemas"]["MfaChallenge"]
    >();
    expectTypeOf(client.auth.verifyMfa).returns.resolves.toEqualTypeOf<
      components["schemas"]["TokenPair"]
    >();
    expectTypeOf(client.projects.list).returns.resolves.toEqualTypeOf<
      components["schemas"]["ProjectOut"][]
    >();
    expectTypeOf(client.chain.status).returns.resolves.toEqualTypeOf<
      components["schemas"]["ChainStatusOut"]
    >();
    expectTypeOf(client.analysis.timeseries).returns.resolves.toEqualTypeOf<
      components["schemas"]["TimeseriesOut"]
    >();
  });

  it("the raw client is typed from the OpenAPI paths", () => {
    type Me = paths["/api/v1/auth/me"]["get"]["responses"][200]["content"]["application/json"];
    expectTypeOf<Me>().toEqualTypeOf<components["schemas"]["UserOut"]>();
    const raw = () => client.api.GET("/api/v1/auth/me");
    expectTypeOf(raw)
      .returns.resolves.toHaveProperty("data")
      .toEqualTypeOf<components["schemas"]["UserOut"] | undefined>();
    // @ts-expect-error unknown paths are rejected at compile time
    void client.api.GET("/api/v1/not-a-route");
  });
});
