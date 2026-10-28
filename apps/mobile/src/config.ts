/**
 * Build-time configuration. `EXPO_PUBLIC_*` variables are inlined by Metro at build time.
 * There is deliberately no default: a build without an API address shows an error instead of
 * silently talking to a guessed host.
 */
export function apiBaseUrl(): string | null {
  const raw = process.env["EXPO_PUBLIC_API_URL"]?.trim();
  if (!raw) return null;
  return raw.replace(/\/+$/, "");
}
