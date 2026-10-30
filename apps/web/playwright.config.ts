import { defineConfig, devices } from "@playwright/test";

// Five local processes back the three kinds of test below. Each has its own port:
//   smoke       next start, no API                        PORT          (3100)
//   console     next start + FEATURE_DEMO, fixture API    CONSOLE_PORT  (3171) + FIXTURE_API_PORT (8793)
//   mocked      next start, registry/market mock API      MOCKED_PORT   (3721) + REGISTRY_API_PORT (4721)
const PORT = Number(process.env.E2E_PORT ?? 3100);
const CONSOLE_PORT = Number(process.env.E2E_CONSOLE_PORT ?? 3171);
const FIXTURE_API_PORT = Number(process.env.MOCK_API_PORT ?? 8793);
const MOCKED_PORT = Number(process.env.E2E_MOCKED_PORT ?? 3721);
const REGISTRY_API_PORT = Number(process.env.E2E_REGISTRY_API_PORT ?? 4721);
// `localhost`, not 127.0.0.1: the BFF compares the browser Origin with the URL Next derives from
// its --hostname, which must match for state-changing calls to pass the CSRF origin check.
export const CONSOLE_URL = `http://localhost:${CONSOLE_PORT}`;
const nextStart = (port: number, hostname = "127.0.0.1") =>
  `node node_modules/next/dist/bin/next start --port ${port} --hostname ${hostname}`;

/**
 * Smoke tests against the production build (`pnpm build` first). They need no API: pages that
 * depend on it must degrade honestly (hidden counts, "demo not available"), which is part of what
 * is tested. Set E2E_BASE_URL to test a running deployment instead.
 */
export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: { trace: "retain-on-failure" },
  projects: [
    {
      name: "chromium",
      testIgnore: /mocked\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        baseURL: process.env.E2E_BASE_URL ?? `http://127.0.0.1:${PORT}`,
      },
    },
    {
      // Next derives request.nextUrl.origin as `localhost` even when bound to 127.0.0.1, which the
      // BFF's same-origin check compares to the browser Origin: so browse via `localhost`.
      // Runs against a server wired to e2e/mock-api.mjs (mocked registry/market API responses).
      name: "mocked",
      testMatch: /mocked\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], baseURL: `http://localhost:${MOCKED_PORT}` },
    },
  ],
  ...(process.env.E2E_BASE_URL
    ? {}
    : {
        webServer: [
          {
            command: nextStart(PORT),
            url: `http://127.0.0.1:${PORT}`,
            reuseExistingServer: !process.env.CI,
            timeout: 60_000,
            env: { AQUA_API_URL: "", NEXT_PUBLIC_API_URL: "", FEATURE_DEMO: "" },
          },
          {
            command: "node e2e/fixtures/mock-api.mjs",
            url: `http://127.0.0.1:${FIXTURE_API_PORT}/api/v1/demo/status`,
            reuseExistingServer: !process.env.CI,
            timeout: 30_000,
            env: { MOCK_API_PORT: String(FIXTURE_API_PORT) },
          },
          {
            command: nextStart(CONSOLE_PORT, "localhost"),
            url: CONSOLE_URL,
            reuseExistingServer: !process.env.CI,
            timeout: 60_000,
            env: {
              AQUA_API_URL: `http://127.0.0.1:${FIXTURE_API_PORT}`,
              NEXT_PUBLIC_API_URL: "",
              FEATURE_DEMO: "1",
              COOKIE_INSECURE: "1",
            },
          },
          {
            command: "node e2e/mock-api.mjs",
            url: `http://127.0.0.1:${REGISTRY_API_PORT}/api/v1/public/vintages`,
            reuseExistingServer: false,
            timeout: 30_000,
            env: { MOCK_API_PORT: String(REGISTRY_API_PORT) },
          },
          {
            command: nextStart(MOCKED_PORT),
            url: `http://127.0.0.1:${MOCKED_PORT}`,
            reuseExistingServer: false,
            timeout: 60_000,
            env: {
              AQUA_API_URL: `http://127.0.0.1:${REGISTRY_API_PORT}`,
              NEXT_PUBLIC_API_URL: "",
              FEATURE_DEMO: "",
              COOKIE_INSECURE: "1",
            },
          },
        ],
      }),
});
