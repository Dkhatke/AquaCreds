import type { AlertOut, ProjectOut, Schema, SubmissionOut } from "@aquacreds/api-client";
import { describe, expect, it } from "vitest";
import {
  buildAttention,
  latestReport,
  type ProjectSummary,
  periodActivity,
  pollDelay,
  trustBands,
} from "./overview-data";

type Report = Schema<"ReportOut">;
const project = (over: Partial<ProjectOut> = {}) =>
  ({ id: "p1", name: "Plot A", status: "monitoring", ...over }) as ProjectOut;
const report = (status: Report["status"], at: string) =>
  ({ id: at, status, generated_at: at }) as Report;
const sub = (status: SubmissionOut["status"], trust: number | null) =>
  ({ status, trust_score: trust }) as SubmissionOut;
const alert = (severity: AlertOut["severity"], ack: string | null = null) =>
  ({ severity, acknowledged_at: ack }) as AlertOut;

function summary(over: Partial<ProjectSummary> = {}): ProjectSummary {
  return {
    project: project(),
    classification: { ok: true, data: null },
    ndvi: { ok: true, data: null },
    openAlerts: { ok: true, data: [] },
    periods: { ok: true, data: [] },
    reports: { ok: true, data: [] },
    submissions: { ok: true, data: [] },
    ...over,
  };
}
const mfa = (over: Partial<Schema<"MfaStatusOut">> = {}): Schema<"MfaStatusOut"> => ({
  enabled: true,
  enabled_at: "2026-01-01T00:00:00Z",
  recovery_codes_remaining: 10,
  required: false,
  ...over,
});

describe("buildAttention", () => {
  it("is empty when nothing needs attention", () => {
    expect(buildAttention({ summaries: [summary()], platformRole: "user", mfa: mfa() })).toEqual(
      [],
    );
  });

  it("queues pending reviews only for verifiers and registry admins", () => {
    const s = summary({
      reports: { ok: true, data: [report("under_review", "1"), report("approved", "2")] },
    });
    expect(buildAttention({ summaries: [s], platformRole: "user", mfa: null })).toEqual([]);
    const v = buildAttention({ summaries: [s], platformRole: "verifier", mfa: null });
    expect(v).toMatchObject([{ kind: "pendingReview", count: 1 }]);
  });

  it("flags changes requested on the latest report and on the project", () => {
    const s = summary({
      project: project({ status: "changes_requested" }),
      reports: {
        ok: true,
        data: [report("approved", "2026-01-01"), report("changes_requested", "2026-03-01")],
      },
    });
    const kinds = buildAttention({ summaries: [s], platformRole: "user", mfa: null }).map(
      (a) => a.kind,
    );
    expect(kinds).toEqual(["projectChanges", "changesRequested"]);
  });

  it("does not flag an old changes-requested report that was superseded", () => {
    const s = summary({
      reports: {
        ok: true,
        data: [report("changes_requested", "2026-01-01"), report("approved", "2026-03-01")],
      },
    });
    expect(buildAttention({ summaries: [s], platformRole: "user", mfa: null })).toEqual([]);
  });

  it("counts failed submissions across periods and flags open alerts by severity", () => {
    const s = summary({
      openAlerts: { ok: true, data: [alert("high"), alert("low")] },
      submissions: {
        ok: true,
        data: [
          { period: {} as never, items: [sub("rejected", 20), sub("checked", 90)] },
          { period: {} as never, items: [sub("rejected", 10)] },
        ],
      },
    });
    const out = buildAttention({ summaries: [s], platformRole: "user", mfa: null });
    expect(out.find((a) => a.kind === "failedSubmissions")?.count).toBe(2);
    expect(out.find((a) => a.kind === "openAlerts")).toMatchObject({
      count: 2,
      severity: "failed",
    });
  });

  it("warns on low recovery codes and on missing required MFA", () => {
    const low = buildAttention({
      summaries: [],
      platformRole: "user",
      mfa: mfa({ recovery_codes_remaining: 2 }),
    });
    expect(low).toMatchObject([{ kind: "lowCodes", count: 2, severity: "review" }]);
    const none = buildAttention({
      summaries: [],
      platformRole: "user",
      mfa: mfa({ recovery_codes_remaining: 0 }),
    });
    expect(none[0]?.severity).toBe("failed");
    const req = buildAttention({
      summaries: [],
      platformRole: "user",
      mfa: mfa({ enabled: false, required: true }),
    });
    expect(req.map((a) => a.kind)).toEqual(["mfaRequired"]);
  });

  it("adds nothing for a part that failed to load (no invented counts)", () => {
    const failed = { ok: false as const, error: "boom" };
    const s = summary({ reports: failed, openAlerts: failed, submissions: failed });
    expect(buildAttention({ summaries: [s], platformRole: "verifier", mfa: null })).toEqual([]);
  });
});

describe("field activity", () => {
  it("bands trust scores in tens and skips unscored submissions", () => {
    const bands = trustBands([
      sub("checked", 0),
      sub("checked", 9),
      sub("checked", 95),
      sub("checked", 100),
      sub("pending_checks", null),
    ]);
    expect(bands).toHaveLength(10);
    expect(bands[0]).toMatchObject({ from: 0, to: 9, count: 2 });
    expect(bands[9]).toMatchObject({ from: 90, to: 100, count: 2 });
    expect(bands.reduce((n, b) => n + b.count, 0)).toBe(4);
  });

  it("summarises submissions per period", () => {
    const s = summary({
      submissions: {
        ok: true,
        data: [
          {
            period: { id: "x" } as never,
            items: [sub("checked", 80), sub("rejected", 10), sub("pending_checks", null)],
          },
        ],
      },
    });
    expect(periodActivity([s])).toMatchObject([{ total: 3, checked: 1, rejected: 1, pending: 1 }]);
  });
});

describe("misc", () => {
  it("picks the newest report", () => {
    expect(
      latestReport([report("draft", "2026-01-01"), report("approved", "2026-02-01")])?.id,
    ).toBe("2026-02-01");
    expect(latestReport([])).toBeNull();
  });

  it("backs off the poll interval up to the cap", () => {
    expect([0, 1, 2, 3, 4, 10].map((n) => pollDelay(n))).toEqual([
      30_000, 60_000, 120_000, 240_000, 300_000, 300_000,
    ]);
  });
});
