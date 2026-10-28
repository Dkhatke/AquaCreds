import { CryptoDigestAlgorithm, digest } from "expo-crypto";

/** Lowercase hex SHA-256 of the exact bytes, via expo-crypto's native digest (no SubtleCrypto on Hermes). */
export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const buffer = await digest(CryptoDigestAlgorithm.SHA256, bytes as Uint8Array<ArrayBuffer>);
  return Array.from(new Uint8Array(buffer), (b) => b.toString(16).padStart(2, "0")).join("");
}
