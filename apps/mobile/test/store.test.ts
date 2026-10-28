import { beforeEach, describe, expect, it } from "vitest";
import { CacheStore, migrate, OutboxStore } from "../src/outbox/store";
import type { NewOutboxItem } from "../src/outbox/types";
import { nodeSqlDb, seqId } from "./helpers";

let clock = 1_000;
const now = () => clock;

const photo: NewOutboxItem = {
  kind: "photo",
  projectId: "p1",
  periodId: "q1",
  payload: {
    fileUri: "file:///doc/evidence/a.jpg",
    contentType: "image/jpeg",
    sha256: "a".repeat(64),
    sizeBytes: 10,
    meta: {
      nonce: "n".repeat(40),
      kind: "general",
      lat: 20.7,
      lon: 86.9,
      gps_accuracy_m: 4,
      captured_at: "2026-10-28T06:00:00Z",
      device_model: "",
      app_version: "",
    },
  },
};

describe("OutboxStore (real SQLite)", () => {
  let db: ReturnType<typeof nodeSqlDb>;
  let store: OutboxStore;

  beforeEach(async () => {
    clock = 1_000;
    db = nodeSqlDb();
    await migrate(db);
    store = new OutboxStore(db, seqId, now);
  });

  it("migrates idempotently", async () => {
    await migrate(db);
    const v = db.raw.prepare("PRAGMA user_version").get() as { user_version: number };
    expect(v.user_version).toBe(1);
  });

  it("round-trips items and persists transitions", async () => {
    const added = await store.add(photo);
    expect(added).toMatchObject({ status: "queued", attempts: 0, payload: photo.payload });
    clock = 2_000;
    const sending = await store.apply(added, { type: "start" });
    const again = await store.get(added.id);
    expect(again).toEqual(sending);
    expect(again?.updatedAt).toBe(2_000);
  });

  it("returns due items oldest first, honouring backoff", async () => {
    const a = await store.add(photo);
    clock = 1_100;
    const b = await store.add({ ...photo, periodId: "q2" });
    const sending = await store.apply(a, { type: "start" });
    await store.apply(sending, {
      type: "failed",
      failure: { kind: "retryable", message: "offline" },
    });
    expect((await store.due(clock)).map((i) => i.id)).toEqual([b.id]);
    const waiting = await store.get(a.id);
    expect((await store.due(waiting?.nextAttemptAt ?? 0)).map((i) => i.id)).toEqual([a.id, b.id]);
  });

  it("counts by status and recovers interrupted sends", async () => {
    const a = await store.add(photo);
    await store.add(photo);
    await store.apply(a, { type: "start" });
    expect(await store.counts()).toEqual({ pending: 2, failed: 0, awaitingChecks: 0 });
    expect(await store.recoverInterrupted()).toBe(1);
    expect((await store.get(a.id))?.status).toBe("queued");
  });

  it("stores the server result and removes items", async () => {
    const a = await store.apply(await store.add(photo), { type: "start" });
    const result = {
      status: "checked" as const,
      trustScore: 60,
      checks: [{ name: "exif_gps", outcome: "warn" as const, reason: "far", hard: false }],
    };
    await store.apply(a, { type: "accepted", serverId: "sub-1", result });
    expect(await store.get(a.id)).toMatchObject({ status: "done", serverId: "sub-1", result });
    await store.remove(a.id);
    expect(await store.get(a.id)).toBeNull();
  });
});

describe("CacheStore", () => {
  it("upserts JSON values with a timestamp", async () => {
    const db = nodeSqlDb();
    await migrate(db);
    const cache = new CacheStore(db, now);
    clock = 5;
    await cache.set("projects", [{ id: 1 }]);
    clock = 6;
    await cache.set("projects", [{ id: 2 }]);
    expect(await cache.get("projects")).toEqual({ value: [{ id: 2 }], updatedAt: 6 });
    await cache.remove("projects");
    expect(await cache.get("projects")).toBeNull();
  });
});
