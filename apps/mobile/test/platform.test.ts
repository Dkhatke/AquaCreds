import { createHash } from "node:crypto";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Expo native modules are replaced with in-memory doubles; the adapters' logic runs for real.
const secure = new Map<string, string>();
vi.mock("expo-secure-store", () => ({
  AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY: 1,
  getItemAsync: vi.fn(async (k: string) => secure.get(k) ?? null),
  setItemAsync: vi.fn(async (k: string, v: string) => {
    secure.set(k, v);
  }),
  deleteItemAsync: vi.fn(async (k: string) => {
    secure.delete(k);
  }),
}));

vi.mock("expo-crypto", () => ({
  CryptoDigestAlgorithm: { SHA256: "SHA-256" },
  digest: vi.fn(async (_alg: string, data: Uint8Array) => {
    const out = createHash("sha256").update(data).digest();
    return out.buffer.slice(out.byteOffset, out.byteOffset + out.byteLength);
  }),
}));

const disk = new Map<string, Uint8Array>();
vi.mock("expo-file-system", () => {
  class File {
    uri: string;
    constructor(...parts: (string | { uri: string })[]) {
      this.uri = parts.map((p) => (typeof p === "string" ? p : p.uri)).join("/");
    }
    get exists() {
      return disk.has(this.uri);
    }
    get size() {
      return disk.get(this.uri)?.length ?? 0;
    }
    async bytes() {
      return disk.get(this.uri) as Uint8Array;
    }
    async move(target: File) {
      const b = disk.get(this.uri);
      if (!b) throw new Error("ENOENT");
      disk.delete(this.uri);
      disk.set(target.uri, b);
      this.uri = target.uri;
    }
    delete() {
      disk.delete(this.uri);
    }
  }
  class Directory {
    uri: string;
    exists = true;
    constructor(...parts: (string | { uri: string })[]) {
      this.uri = parts.map((p) => (typeof p === "string" ? p : p.uri)).join("/");
    }
    create() {}
  }
  return { File, Directory, Paths: { document: { uri: "file:///doc" } } };
});

const expoFetch = vi.fn(async () => new Response(null, { status: 200 }));
vi.mock("expo/fetch", () => ({ fetch: expoFetch }));

const { sha256Hex } = await import("../src/platform/sha256");
const { secureTokenStore } = await import("../src/platform/tokens");
const files = await import("../src/platform/files");
const { appFetch } = await import("../src/api/fetch");
const { LocalItemError } = await import("../src/outbox/machine");

beforeEach(() => {
  secure.clear();
  disk.clear();
  expoFetch.mockClear();
});

describe("sha256Hex (expo-crypto)", () => {
  it("returns lowercase hex of the exact bytes", async () => {
    const bytes = new Uint8Array([0, 255, 10, 13]);
    expect(await sha256Hex(bytes)).toBe(createHash("sha256").update(bytes).digest("hex"));
    expect(await sha256Hex(new TextEncoder().encode("abc"))).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
  });
});

describe("secureTokenStore (expo-secure-store)", () => {
  it("persists, caches and clears both tokens", async () => {
    const store = secureTokenStore();
    expect(await store.get()).toBeNull();
    await store.set({ accessToken: "a", refreshToken: "r" });
    expect(secure.get("aquacreds.refresh")).toBe("r");
    expect(await secureTokenStore().get()).toEqual({ accessToken: "a", refreshToken: "r" });
    await store.clear();
    expect(secure.size).toBe(0);
    expect(await store.get()).toBeNull();
  });
});

describe("evidence files (expo-file-system)", () => {
  it("moves the capture without touching its bytes, reads and deletes it", async () => {
    const bytes = new Uint8Array([0xff, 0xd8, 1, 2, 0xff, 0xd9]);
    disk.set("file:///cache/Camera/x.jpg", bytes);
    const uri = await files.persistCapture("file:///cache/Camera/x.jpg", "abc.jpg");
    expect(uri).toBe("file:///doc/evidence/abc.jpg");
    expect(disk.has("file:///cache/Camera/x.jpg")).toBe(false);
    expect(await files.readFileBytes(uri)).toBe(bytes);
    expect(await files.fileSize(uri)).toBe(6);
    await files.deleteFile(uri);
    await expect(files.readFileBytes(uri)).rejects.toBeInstanceOf(LocalItemError);
  });
});

describe("appFetch", () => {
  it("sends binary bodies through expo/fetch unchanged", async () => {
    const body = new Uint8Array([1, 2, 3]);
    await appFetch("https://storage.test/k", {
      method: "PUT",
      body,
      headers: { "Content-Type": "image/jpeg" },
    });
    expect(expoFetch).toHaveBeenCalledWith("https://storage.test/k", {
      method: "PUT",
      body,
      headers: { "Content-Type": "image/jpeg" },
    });
  });

  it("leaves API Request objects to the global fetch", async () => {
    const global = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}"));
    await appFetch(new Request("https://api.test/x"));
    expect(global).toHaveBeenCalledOnce();
    expect(expoFetch).not.toHaveBeenCalled();
    global.mockRestore();
  });
});
