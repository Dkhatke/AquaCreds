import { describe, expect, it } from "vitest";
import { filterSubmissions, NO_FILTER, parseChecks, trustStatus, worstOutcome } from "./evidence";
import {
  defaultStrata,
  explorerTxUrl,
  isConflictOfInterest,
  reviewError,
  validateStrata,
  verificationSummary,
} from "./mrv";

describe("validateStrata", () => {
  it("defaults a single stratum to the project area", () => {
    const rows = defaultStrata(["fringe"], 12.5);
    expect(rows).toEqual([{ stratum: "fringe", included: true, area: "12.5" }]);
    expect(validateStrata(rows, 12.5).payload).toEqual({ fringe: 12.5 });
  });
  it("leaves several strata empty and refuses until filled", () => {
    const rows = defaultStrata(["b", "a"], 10);
    expect(rows.map((r) => r.stratum)).toEqual(["a", "b"]);
    expect(validateStrata(rows, 10).payload).toBeNull();
  });
  it("rejects a sum above the project area and non-positive values", () => {
    const over = validateStrata(
      [
        { stratum: "a", included: true, area: "6" },
        { stratum: "b", included: true, area: "5" },
      ],
      10,
    );
    expect(over.errors.join(" ")).toMatch(/exceed the project area/);
    const zero = validateStrata([{ stratum: "a", included: true, area: "0" }], 10);
    expect(zero.payload).toBeNull();
    expect(validateStrata([{ stratum: "a", included: false, area: "" }], 10).errors[0]).toMatch(
      /at least one/,
    );
  });
  it("allows a sum equal to the project area and ignores excluded strata", () => {
    const ok = validateStrata(
      [
        { stratum: "a", included: true, area: "4" },
        { stratum: "b", included: true, area: "6" },
        { stratum: "c", included: false, area: "" },
      ],
      10,
    );
    expect(ok.payload).toEqual({ a: 4, b: 6 });
  });
});

describe("review helpers", () => {
  it("requires a comment unless approving", () => {
    expect(reviewError("approve", "")).toBeNull();
    expect(reviewError("reject", "  ")).toMatch(/required/);
    expect(reviewError("request_changes", "fix the plot areas")).toBeNull();
  });
  it("recognises the conflict-of-interest message", () => {
    expect(
      isConflictOfInterest("Conflict of interest: verifiers cannot review projects of their own"),
    ).toBe(true);
    expect(isConflictOfInterest("Report is approved")).toBe(false);
  });
});

describe("chain and hash helpers", () => {
  it("links only known explorers", () => {
    expect(explorerTxUrl(80002, "0xabc")).toBe("https://amoy.polygonscan.com/tx/0xabc");
    expect(explorerTxUrl(1337, "0xabc")).toBeNull();
  });
  it("summarises verification, including not-yet-anchored", () => {
    const base = {
      report_id: "r",
      report_hash: "h",
      stored_hash: "h",
      stored_matches: true,
      onchain_hash: null,
      onchain_source: null,
      onchain_matches: null,
      ok: true,
    } as const;
    const s = verificationSummary(base);
    expect(s.stored.status).toBe("ok");
    expect(s.onchain.status).toBe("neutral");
    const bad = verificationSummary({
      ...base,
      onchain_hash: "x",
      onchain_matches: false,
      ok: false,
    });
    expect(bad.onchain.status).toBe("failed");
    expect(
      verificationSummary({ ...base, stored_hash: null, stored_matches: false }).stored.label,
    ).toMatch(/missing/);
  });
});

const sub = (over: Record<string, unknown>) =>
  ({
    id: "s",
    period_id: "p",
    kind: "plot_centre",
    status: "checked",
    plot_id: null,
    sha256: "a".repeat(64),
    lat: 0,
    lon: 0,
    gps_accuracy_m: 5,
    captured_at: "2026-01-01T00:00:00Z",
    received_at: "2026-01-01T00:00:00Z",
    trust_score: 80,
    checks: null,
    exif: null,
    ...over,
  }) as never;

describe("evidence helpers", () => {
  it("drops malformed checks and keeps well-formed ones", () => {
    const c = parseChecks([
      { name: "geofence", outcome: "pass", reason: "Inside" },
      { name: "x", outcome: "bogus", reason: "?" },
      { outcome: "pass" },
    ]);
    expect(c).toHaveLength(1);
    expect(c[0]?.hard).toBe(false);
    expect(parseChecks(null)).toEqual([]);
  });
  it("finds the worst outcome ignoring skipped checks", () => {
    const mk = (outcome: "pass" | "warn" | "fail" | "skipped") => ({
      name: "n",
      outcome,
      reason: "",
      hard: false,
      details: null,
    });
    expect(worstOutcome([mk("skipped")])).toBeNull();
    expect(worstOutcome([mk("pass"), mk("warn")])).toBe("warn");
    expect(worstOutcome([mk("warn"), mk("fail")])).toBe("fail");
  });
  it("bands trust scores", () => {
    expect(trustStatus(null)).toBe("neutral");
    expect(trustStatus(100)).toBe("ok");
    expect(trustStatus(60)).toBe("review");
    expect(trustStatus(0)).toBe("failed");
  });
  it("filters by status, worst result and kind", () => {
    const a = sub({ id: "a", checks: [{ name: "geofence", outcome: "fail", reason: "" }] });
    const b = sub({
      id: "b",
      kind: "general",
      checks: [{ name: "geofence", outcome: "pass", reason: "" }],
    });
    const c = sub({ id: "c", status: "rejected" });
    const all = [a, b, c];
    expect(filterSubmissions(all, NO_FILTER)).toHaveLength(3);
    expect(filterSubmissions(all, { ...NO_FILTER, outcome: "fail" })).toEqual([a]);
    expect(filterSubmissions(all, { ...NO_FILTER, kind: "general" })).toEqual([b]);
    expect(filterSubmissions(all, { ...NO_FILTER, status: "rejected" })).toEqual([c]);
  });
});
