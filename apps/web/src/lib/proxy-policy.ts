/**
 * Which API paths the browser may reach through the BFF proxy (`/bff/api/...`).
 * Token-bearing auth endpoints are only reachable through the dedicated `/bff/auth/*` handlers,
 * so access/refresh tokens never appear in a response that client-side JavaScript can read.
 */
const AUTH_READS = new Set(["me", "mfa"]);

export function proxyable(method: string, segments: string[]): boolean {
  if (segments.some((s) => s === "" || s === "." || s === ".." || s.includes("/"))) return false;
  if (segments[0] !== "v1" || segments.length < 2) return false;
  if (segments[1] === "demo") return false; // dedicated handler: it returns a token
  if (segments[1] === "auth") {
    return method === "GET" && segments.length === 3 && AUTH_READS.has(segments[2] ?? "");
  }
  return true;
}
