import { createHash } from "node:crypto";
import { createAquaClient, memoryTokenStore } from "@aquacreds/api-client";
import { beforeEach, describe, expect, it } from "vitest";
import { LocalItemError } from "../src/outbox/machine";
import { migrate, OutboxStore } from "../src/outbox/store";
import { SyncEngine } from "../src/outbox/sync";
import type { NewOutboxItem } from "../src/outbox/types";
import { json, mockFetch, nodeSqlDb, seqId } from "./helpers";

const API = "https://api.test";
const STORAGE = "https://storage.test";
const PERIOD = "11111111-1111-4111-8111-111111111111";
const PROJECT = "22222222-2222-4222-8222-222222222222";
const PLOT = "33333333-3333-4333-8333-333333333333";
const KEY = `projects/${PROJECT}/periods/${PERIOD}/photos/abc.jpg`;

// JPEG-ish bytes including values that a text/base64 round trip would mangle.
const PHOTO = new Uint8Array([
  0xff, 0xd8, 0xff, 0xe1, 0x00, 0x80, 0x0d, 0x0a, 0xc3, 0x28, 0xff, 0xd9,
]);
const hex = (b: Uint8Array) => createHash("sha256").update(b).digest("hex");
const PHOTO_SHA = hex(PHOTO);

const META = {
  nonce: "n".repeat(43),
  kind: "general" as const,
  lat: 20.705,
  lon: 86.905,
  gps_accuracy_m: 4.5,
  heading_deg: 270,
  captured_at: "2026-10-28T06:00:00.000Z",
  device_model: "Pixel 4a",
  app_version: "0.1.0",
};

const photoItem: NewOutboxItem = {
  kind: "photo",
  projectId: PROJECT,
  periodId: PERIOD,
  payload: {
    fileUri: "file:///doc/evidence/a.jpg",
    contentType: "image/jpeg",
    sha256: PHOTO_SHA,
    sizeBytes: PHOTO.length,
    meta: META,
  },
};

function submission(over: Record<string, unknown> = {}) {
  return {
    id: "sub-1",
    period_id: PERIOD,
    kind: "general",
    status: "pending_checks",
    plot_id: null,
    sha256: PHOTO_SHA,
    lat: META.lat,
    lon: META.lon,
    gps_accuracy_m: META.gps_accuracy_m,
    captured_at: META.captured_at,
    received_at: "2026-10-28T06:05:00Z",
    trust_score: null,
    checks: null,
    exif: null,
    ...over,
  };
}

const ticket = () =>
  json(200, {
    object_key: KEY,
    upload_url: `${STORAGE}/bucket/${KEY}?X-Amz-Signature=sig`,
    method: "PUT",
    headers: { "Content-Type": "image/jpeg" },
    expires_in: 900,
  });

let clock = 1_000_000;

function setup(
  routes: Parameters<typeof mockFetch>[0],
  files = new Map([["file:///doc/evidence/a.jpg", PHOTO]]),
) {
  const http = mockFetch(routes);
  const db = nodeSqlDb();
  const store = new OutboxStore(db, seqId, () => clock);
  const deleted: string[] = [];
  const client = createAquaClient({
    baseUrl: API,
    fetch: http.fetch,
    tokenStore: memoryTokenStore({ accessToken: "acc", refreshToken: "ref" }),
    sha256: async (b) => hex(b),
  });
  const engine = new SyncEngine({
    client,
    store,
    readFile: async (uri) => {
      const bytes = files.get(uri);
      if (!bytes) throw new LocalItemError("missing");
      return bytes;
    },
    sha256: async (b) => hex(b),
    deleteFile: async (uri) => {
      deleted.push(uri);
    },
    now: () => clock,
  });
  return { http, db, store, engine, deleted, files };
}

const UPLOAD_URL = `POST /api/v1/periods/${PERIOD}/photos/upload-url`;
const SUBMIT = `POST /api/v1/periods/${PERIOD}/submissions`;
const LIST = `GET /api/v1/periods/${PERIOD}/submissions`;
const PUT = `PUT /bucket/${KEY}`;

beforeEach(() => {
  clock = 1_000_000;
});

describe("photo upload flow", () => {
  it("uploads the exact bytes, submits the hash + metadata, then records the check result", async () => {
    let polls = 0;
    const { http, db, store, engine, deleted } = setup({
      [UPLOAD_URL]: () => ticket(),
      [PUT]: () => new Response(null, { status: 200 }),
      [SUBMIT]: () => json(202, submission()),
      "GET /api/v1/submissions/sub-1": () =>
        ++polls === 1
          ? json(200, submission())
          : json(
              200,
              submission({
                status: "checked",
                trust_score: 80,
                checks: [
                  { name: "exif_gps", outcome: "pass", reason: "EXIF GPS within 3 m", hard: false },
                  { name: "exif_time", outcome: "warn", reason: "no EXIF time", hard: false },
                ],
              }),
            ),
    });
    await migrate(db);
    const item = await store.add(photoItem);

    const first = await engine.sync();
    expect(first).toMatchObject({ sent: 1, retrying: 0, failed: 0, needsSignIn: false });

    const put = http.callsTo(PUT)[0]!;
    expect(put.body).toEqual(PHOTO); // byte-for-byte, no re-encoding
    expect(put.headers.get("content-type")).toBe("image/jpeg");
    expect(put.headers.get("authorization")).toBeNull(); // presigned: no API token leaks
    const ticketReq = http.callsTo(UPLOAD_URL)[0]!;
    expect(JSON.parse(new TextDecoder().decode(ticketReq.body))).toEqual({
      content_type: "image/jpeg",
    });
    const submit = http.callsTo(SUBMIT)[0]!;
    expect(submit.headers.get("authorization")).toBe("Bearer acc");
    expect(JSON.parse(new TextDecoder().decode(submit.body))).toEqual({
      ...META,
      object_key: KEY,
      sha256: PHOTO_SHA,
    });
    expect(await store.get(item.id)).toMatchObject({
      status: "awaiting_checks",
      serverId: "sub-1",
    });
    expect(deleted).toEqual([]);

    const second = await engine.sync();
    expect(second.checked).toBe(1);
    expect(polls).toBe(2);
    const done = await store.get(item.id);
    expect(done).toMatchObject({ status: "done", result: { status: "checked", trustScore: 80 } });
    expect(done?.result?.checks.map((c) => c.outcome)).toEqual(["pass", "warn"]);
    expect(deleted).toEqual(["file:///doc/evidence/a.jpg"]);
  });

  it("retries with backoff when offline, then succeeds", async () => {
    let online = false;
    const offline = () => {
      throw new TypeError("Network request failed");
    };
    const { store, db, engine } = setup({
      [UPLOAD_URL]: () => (online ? ticket() : offline()),
      [PUT]: () => new Response(null, { status: 200 }),
      [SUBMIT]: () => json(202, submission({ status: "rejected", trust_score: 0 })),
    });
    await migrate(db);
    const item = await store.add(photoItem);

    expect(await engine.sync()).toMatchObject({ sent: 0, retrying: 1 });
    const waiting = await store.get(item.id);
    expect(waiting).toMatchObject({ status: "retry_wait", attempts: 1 });
    expect(waiting?.lastError).toContain("Network request failed");

    online = true;
    expect((await engine.sync()).sent).toBe(0); // not due yet
    clock = waiting?.nextAttemptAt ?? 0;
    expect((await engine.sync()).sent).toBe(1);
    expect(await store.get(item.id)).toMatchObject({
      status: "done",
      attempts: 2,
      result: { status: "rejected", trustScore: 0 },
    });
  });

  it("refuses to send a file whose bytes changed after capture", async () => {
    const changed = new Map([["file:///doc/evidence/a.jpg", new Uint8Array([1, 2, 3])]]);
    const { http, store, db, engine } = setup({}, changed);
    await migrate(db);
    const item = await store.add(photoItem);
    expect((await engine.sync()).failed).toBe(1);
    expect(http.calls).toHaveLength(0);
    expect((await store.get(item.id))?.status).toBe("failed");
  });

  it("fails permanently with the server's reason on a 4xx", async () => {
    const { store, db, engine } = setup({
      [UPLOAD_URL]: () => ticket(),
      [PUT]: () => new Response(null, { status: 200 }),
      [SUBMIT]: () => json(422, { detail: "Unknown capture nonce for this user and period" }),
    });
    await migrate(db);
    const item = await store.add(photoItem);
    await engine.sync();
    expect(await store.get(item.id)).toMatchObject({
      status: "failed",
      lastError: "Unknown capture nonce for this user and period",
    });
  });

  it("retries when the presigned URL has expired (storage 403)", async () => {
    const { store, db, engine } = setup({
      [UPLOAD_URL]: () => ticket(),
      [PUT]: () => new Response("<Error>AccessDenied</Error>", { status: 403 }),
    });
    await migrate(db);
    const item = await store.add(photoItem);
    expect((await engine.sync()).retrying).toBe(1);
    expect((await store.get(item.id))?.status).toBe("retry_wait");
  });

  it("recovers when an earlier attempt succeeded but the response was lost", async () => {
    const { http, store, db, engine } = setup({
      [UPLOAD_URL]: () => ticket(),
      [PUT]: () => new Response(null, { status: 200 }),
      [SUBMIT]: () => json(409, { detail: "Capture nonce already used" }),
      [LIST]: () =>
        json(200, [
          submission({ id: "other", sha256: "f".repeat(64) }),
          submission({ id: "mine" }),
        ]),
    });
    await migrate(db);
    const item = await store.add(photoItem);
    expect((await engine.sync()).sent).toBe(1);
    expect(http.callsTo(LIST)).toHaveLength(1);
    expect(await store.get(item.id)).toMatchObject({ status: "awaiting_checks", serverId: "mine" });
  });

  it("fails when the nonce was consumed by a different photo", async () => {
    const { store, db, engine } = setup({
      [UPLOAD_URL]: () => ticket(),
      [PUT]: () => new Response(null, { status: 200 }),
      [SUBMIT]: () => json(409, { detail: "Capture nonce already used" }),
      [LIST]: () => json(200, [submission({ id: "other", sha256: "f".repeat(64) })]),
    });
    await migrate(db);
    const item = await store.add(photoItem);
    expect((await engine.sync()).failed).toBe(1);
    expect((await store.get(item.id))?.status).toBe("failed");
  });

  it("pauses everything when the session has ended", async () => {
    const { http, store, db, engine } = setup({
      [UPLOAD_URL]: () => json(401, { detail: "Token expired" }),
      "POST /api/v1/auth/refresh": () => json(401, { detail: "Refresh token revoked" }),
    });
    await migrate(db);
    const a = await store.add(photoItem);
    const b = await store.add(photoItem);
    const summary = await engine.sync();
    expect(summary.needsSignIn).toBe(true);
    expect(await store.get(a.id)).toMatchObject({ status: "queued", attempts: 0 });
    expect((await store.get(b.id))?.status).toBe("queued");
    expect(http.callsTo(UPLOAD_URL)).toHaveLength(1); // b was not attempted
  });

  it("shares one pass between concurrent sync calls", async () => {
    const { http, store, db, engine } = setup({
      [UPLOAD_URL]: () => ticket(),
      [PUT]: () => new Response(null, { status: 200 }),
      [SUBMIT]: () => json(202, submission()),
    });
    await migrate(db);
    await store.add(photoItem);
    const [x, y] = await Promise.all([engine.sync(), engine.sync()]);
    expect(x).toBe(y);
    expect(http.callsTo(SUBMIT)).toHaveLength(1);
  });
});

describe("measurement flow", () => {
  const MEASURE = `POST /api/v1/periods/${PERIOD}/plots/${PLOT}/measurements`;
  const body = {
    measured_on: "2026-10-27",
    trees: [{ species: "Avicennia marina", dbh_cm: 8.2, height_m: 4, alive: true }],
    notes: "",
  };

  it("posts the validated body and completes", async () => {
    const { http, store, db, engine } = setup({
      [MEASURE]: () => json(201, { id: "m-1", plot_id: PLOT, period_id: PERIOD, ...body }),
    });
    await migrate(db);
    const item = await store.add({
      kind: "measurement",
      projectId: PROJECT,
      periodId: PERIOD,
      payload: { plotId: PLOT, plotCode: "P-01", body },
    });
    expect((await engine.sync()).sent).toBe(1);
    expect(JSON.parse(new TextDecoder().decode(http.callsTo(MEASURE)[0]!.body))).toEqual(body);
    expect(await store.get(item.id)).toMatchObject({
      status: "done",
      serverId: "m-1",
      result: { status: "recorded" },
    });
  });

  it("surfaces a duplicate measurement as needing attention", async () => {
    const { store, db, engine } = setup({
      [MEASURE]: () => json(409, { detail: "This plot was already measured in this period" }),
    });
    await migrate(db);
    const item = await store.add({
      kind: "measurement",
      projectId: PROJECT,
      periodId: PERIOD,
      payload: { plotId: PLOT, plotCode: "P-01", body },
    });
    await engine.sync();
    expect(await store.get(item.id)).toMatchObject({
      status: "failed",
      lastError: "This plot was already measured in this period",
    });
  });
});
