import { describe, expect, it } from "vitest";
import {
  ApiError,
  createAquaClient,
  memoryTokenStore,
  presignedUpload,
  sha256Hex,
  UploadError,
} from "../src";
import { json, mockFetch } from "./mock-fetch";

const PHOTO = new TextEncoder().encode("hello");
const PHOTO_SHA = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";
const PERIOD = "11111111-1111-1111-1111-111111111111";
const KEY = `projects/p/periods/${PERIOD}/photos/abc.jpg`;
const ticket = {
  object_key: KEY,
  upload_url: "https://s3.test/aquacreds/abc.jpg?X-Amz-Signature=sig",
  method: "PUT",
  headers: { "Content-Type": "image/jpeg" },
  expires_in: 600,
};
const meta = {
  nonce: "n".repeat(43),
  kind: "photo_point",
  lat: 21.9,
  lon: 88.8,
  gps_accuracy_m: 4.5,
  captured_at: "2026-10-20T05:00:00Z",
  device_model: "Pixel 9",
  app_version: "0.1.0",
} as const;

function setup(putStatus = 200) {
  const mock = mockFetch({
    [`POST /api/v1/periods/${PERIOD}/photos/upload-url`]: () => json(200, ticket),
    "PUT /aquacreds/abc.jpg": () => new Response(null, { status: putStatus }),
    [`POST /api/v1/periods/${PERIOD}/submissions`]: (c) =>
      json(202, { id: "s1", ...JSON.parse(c.body) }),
  });
  const client = createAquaClient({
    baseUrl: "http://api.test",
    fetch: mock.fetch,
    tokenStore: memoryTokenStore({ accessToken: "A1", refreshToken: "R1" }),
  });
  return { ...mock, client };
}

describe("sha256Hex", () => {
  it("hashes with SubtleCrypto", async () => {
    expect(await sha256Hex(PHOTO)).toBe(PHOTO_SHA);
  });
});

describe("presigned upload", () => {
  it("requests a URL, PUTs the bytes with ticket headers, and confirms with the SHA-256", async () => {
    const t = setup();
    const sub = await t.client.field.uploadPhotoSubmission(
      PERIOD,
      new Blob([PHOTO]),
      "image/jpeg",
      meta,
    );

    expect(t.calls.map((c) => `${c.method} ${c.url.host}${c.url.pathname}`)).toEqual([
      `POST api.test/api/v1/periods/${PERIOD}/photos/upload-url`,
      "PUT s3.test/aquacreds/abc.jpg",
      `POST api.test/api/v1/periods/${PERIOD}/submissions`,
    ]);
    expect(JSON.parse(t.calls[0]!.body)).toEqual({ content_type: "image/jpeg" });

    const put = t.calls[1]!;
    expect(put.url.href).toBe(ticket.upload_url);
    expect(put.headers.get("Content-Type")).toBe("image/jpeg");
    expect(put.headers.has("Authorization")).toBe(false); // would break the presigned signature
    expect(put.body).toBe("hello");

    expect(JSON.parse(t.calls[2]!.body)).toEqual({ ...meta, object_key: KEY, sha256: PHOTO_SHA });
    expect(sub).toMatchObject({ id: "s1", sha256: PHOTO_SHA });
  });

  it("throws UploadError and does not confirm when storage rejects the PUT", async () => {
    const t = setup(403);
    await expect(
      t.client.field.uploadPhotoSubmission(PERIOD, PHOTO, "image/jpeg", meta),
    ).rejects.toBeInstanceOf(UploadError);
    expect(t.callsTo(`POST /api/v1/periods/${PERIOD}/submissions`)).toHaveLength(0);
  });

  it("propagates confirm errors (e.g. server-side hash mismatch)", async () => {
    const mock = mockFetch({ "PUT /k": () => new Response(null, { status: 200 }) });
    await expect(
      presignedUpload({
        requestTicket: async () => ({ ...ticket, upload_url: "https://s3.test/k" }),
        body: PHOTO.buffer,
        fetch: mock.fetch,
        confirm: async () => {
          throw new ApiError(422, { detail: "SHA-256 mismatch" });
        },
      }),
    ).rejects.toMatchObject({ status: 422, detail: "SHA-256 mismatch" });
  });

  it("uses an injected sha256 implementation (React Native without SubtleCrypto)", async () => {
    const mock = mockFetch({ "PUT /k": () => new Response(null, { status: 200 }) });
    const seen = await presignedUpload({
      requestTicket: async () => ({ ...ticket, upload_url: "https://s3.test/k" }),
      body: PHOTO,
      fetch: mock.fetch,
      sha256: async (bytes) => `len-${bytes.byteLength}`,
      confirm: async ({ sha256 }) => sha256,
    });
    expect(seen).toBe("len-5");
  });
});
