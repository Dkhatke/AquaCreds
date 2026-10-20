import { UploadError } from "./errors";
import type { UploadTicket } from "./types";

export type UploadBody = Blob | ArrayBuffer | Uint8Array;
export type Sha256Fn = (bytes: Uint8Array) => Promise<string>;

function toHex(buffer: ArrayBuffer): string {
  return Array.from(new Uint8Array(buffer), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** SHA-256 (lowercase hex) via SubtleCrypto (browsers, Node >= 20, RN with a WebCrypto polyfill). */
export const sha256Hex: Sha256Fn = async (bytes) => {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) {
    throw new Error(
      "SubtleCrypto is unavailable; pass a `sha256` implementation (e.g. built on expo-crypto)",
    );
  }
  return toHex(await subtle.digest("SHA-256", bytes as Uint8Array<ArrayBuffer>));
};

async function toBytes(body: UploadBody): Promise<Uint8Array> {
  if (body instanceof Uint8Array) return body;
  if (body instanceof ArrayBuffer) return new Uint8Array(body);
  return new Uint8Array(await body.arrayBuffer());
}

export interface PresignedUploadOptions<T> {
  /** Asks the API for a presigned URL (e.g. `POST /periods/{id}/photos/upload-url`). */
  requestTicket: () => Promise<UploadTicket>;
  /** File contents. */
  body: UploadBody;
  /** Tells the API the upload is done; the server re-hashes the object and compares. */
  confirm: (uploaded: { objectKey: string; sha256: string; ticket: UploadTicket }) => Promise<T>;
  /** Plain fetch for the storage PUT (no API auth header: it would break the presigned signature). */
  fetch?: typeof fetch;
  sha256?: Sha256Fn;
  signal?: AbortSignal;
}

/**
 * request URL → hash the bytes → PUT them with the ticket's headers → confirm with the hash.
 * Throws `UploadError` if storage rejects the PUT; confirm errors propagate (e.g. `ApiError` on a
 * SHA-256 mismatch).
 */
export async function presignedUpload<T>(options: PresignedUploadOptions<T>): Promise<T> {
  const doFetch = options.fetch ?? globalThis.fetch;
  const hash = options.sha256 ?? sha256Hex;
  const bytes = await toBytes(options.body);
  const [ticket, sha256] = await Promise.all([options.requestTicket(), hash(bytes)]);

  const init: RequestInit = {
    method: ticket.method || "PUT",
    headers: ticket.headers,
    body: options.body instanceof Blob ? options.body : (bytes as Uint8Array<ArrayBuffer>),
  };
  if (options.signal) init.signal = options.signal;
  const res = await doFetch(ticket.upload_url, init);
  if (!res.ok) {
    throw new UploadError(res.status, `Storage upload failed with HTTP ${res.status}`);
  }
  return options.confirm({ objectKey: ticket.object_key, sha256, ticket });
}
