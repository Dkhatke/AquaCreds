import { ApiError, UploadError } from "@aquacreds/api-client";
import { describe, expect, it } from "vitest";
import {
  BACKOFF_MAX_MS,
  backoffMs,
  classifyError,
  InvalidTransition,
  isDue,
  LocalItemError,
  transition,
} from "../src/outbox/machine";
import type { OutboxItem, OutboxStatus, ServerResult } from "../src/outbox/types";

const T0 = 1_000_000;
const half = () => 0.5; // jitter factor exactly 1

function item(status: OutboxStatus, over: Partial<OutboxItem> = {}): OutboxItem {
  return {
    id: "a",
    kind: "photo",
    projectId: "p",
    periodId: "q",
    status,
    attempts: 0,
    nextAttemptAt: null,
    lastError: null,
    serverId: null,
    result: null,
    createdAt: T0,
    updatedAt: T0,
    payload: {
      fileUri: "file:///x.jpg",
      contentType: "image/jpeg",
      sha256: "0".repeat(64),
      sizeBytes: 3,
      meta: {} as never,
    },
    ...over,
  } as OutboxItem;
}

const pending: ServerResult = { status: "pending_checks", trustScore: null, checks: [] };
const checked: ServerResult = { status: "checked", trustScore: 80, checks: [] };

describe("transition", () => {
  it("runs the happy path for a photo: queued → sending → awaiting_checks → done", () => {
    let i = transition(item("queued"), { type: "start" }, T0);
    expect(i).toMatchObject({ status: "sending", attempts: 1 });
    i = transition(i, { type: "accepted", serverId: "s1", result: pending }, T0 + 1);
    expect(i).toMatchObject({ status: "awaiting_checks", serverId: "s1", updatedAt: T0 + 1 });
    i = transition(i, { type: "checked", result: pending }, T0 + 2);
    expect(i.status).toBe("awaiting_checks");
    i = transition(i, { type: "checked", result: checked }, T0 + 3);
    expect(i).toMatchObject({ status: "done", result: checked });
  });

  it("finishes a measurement as soon as it is accepted", () => {
    const m = { ...item("sending"), kind: "measurement" } as OutboxItem;
    const r = transition(m, { type: "accepted", serverId: "m1", result: pending }, T0);
    expect(r.status).toBe("done");
  });

  it("goes straight to done when the server already finished the checks", () => {
    const r = transition(item("sending"), { type: "accepted", serverId: "s", result: checked }, T0);
    expect(r.status).toBe("done");
  });

  it("backs off on retryable failures and becomes due again later", () => {
    const sending = transition(item("queued"), { type: "start" }, T0);
    const r = transition(
      sending,
      { type: "failed", failure: { kind: "retryable", message: "offline" } },
      T0,
      half,
    );
    expect(r).toMatchObject({
      status: "retry_wait",
      lastError: "offline",
      nextAttemptAt: T0 + 15_000,
    });
    expect(isDue(r, T0 + 14_999)).toBe(false);
    expect(isDue(r, T0 + 15_000)).toBe(true);
    expect(() => transition(r, { type: "start" }, T0)).toThrow(InvalidTransition);
    expect(transition(r, { type: "start" }, T0 + 15_000)).toMatchObject({
      status: "sending",
      attempts: 2,
    });
  });

  it("parks permanent failures until the user retries", () => {
    const r = transition(
      item("sending", { attempts: 1 }),
      { type: "failed", failure: { kind: "permanent", message: "bad" } },
      T0,
    );
    expect(r.status).toBe("failed");
    expect(isDue(r, T0 + 1e9)).toBe(false);
    expect(transition(r, { type: "retry" }, T0)).toMatchObject({ status: "queued" });
  });

  it("does not count auth failures as attempts", () => {
    const r = transition(
      item("sending", { attempts: 3 }),
      { type: "failed", failure: { kind: "auth", message: "Session expired" } },
      T0,
    );
    expect(r).toMatchObject({ status: "queued", attempts: 2 });
  });

  it("requeues items interrupted mid-send", () => {
    expect(transition(item("sending"), { type: "interrupted" }, T0).status).toBe("queued");
  });

  it("rejects invalid transitions", () => {
    const bad: [OutboxStatus, Parameters<typeof transition>[1]][] = [
      ["done", { type: "start" }],
      ["failed", { type: "start" }],
      ["awaiting_checks", { type: "start" }],
      ["queued", { type: "accepted", serverId: "x", result: pending }],
      ["sending", { type: "checked", result: checked }],
      ["queued", { type: "failed", failure: { kind: "permanent", message: "" } }],
      ["queued", { type: "interrupted" }],
      ["done", { type: "retry" }],
      ["awaiting_checks", { type: "retry" }],
    ];
    for (const [status, event] of bad) {
      expect(() => transition(item(status), event, T0), `${status} + ${event.type}`).toThrow(
        InvalidTransition,
      );
    }
  });
});

describe("backoffMs", () => {
  it("doubles from 15 s and caps at 30 min, with ±20% jitter", () => {
    expect([1, 2, 3, 4].map((n) => backoffMs(n, half))).toEqual([15_000, 30_000, 60_000, 120_000]);
    expect(backoffMs(50, half)).toBe(BACKOFF_MAX_MS);
    expect(backoffMs(1, () => 0)).toBe(12_000);
    expect(backoffMs(1, () => 0.999_999)).toBe(18_000);
    expect(backoffMs(50, () => 0.999_999)).toBe(BACKOFF_MAX_MS);
  });
});

describe("classifyError", () => {
  it("maps API, storage, network and local errors", () => {
    expect(classifyError(new ApiError(401, { detail: "x" })).kind).toBe("auth");
    for (const s of [408, 429, 500, 502, 503]) {
      expect(classifyError(new ApiError(s, null)).kind).toBe("retryable");
    }
    expect(classifyError(new ApiError(422, { detail: "Unknown plot P-9" }))).toEqual({
      kind: "permanent",
      message: "Unknown plot P-9",
    });
    expect(
      classifyError(new ApiError(422, { detail: [{ msg: "too big" }, { msg: "bad date" }] }))
        .message,
    ).toBe("too big; bad date");
    expect(classifyError(new UploadError(403, "expired")).kind).toBe("retryable");
    expect(classifyError(new TypeError("Network request failed")).kind).toBe("retryable");
    expect(classifyError(new LocalItemError("gone")).kind).toBe("permanent");
  });
});
