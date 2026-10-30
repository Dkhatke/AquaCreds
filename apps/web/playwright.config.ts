import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.E2E_PORT ?? 3100);
// Second server for console tests: FEATURE_DEMO on, API = the fixture server in e2e/fixtures.
const CONSOLE_PORT = Number(process.env.E2E_CONSOLE_PORT ?? 3171);
const MOCK_PORT = Number(process.env.MOCK_API_PORT ?? 8793);
// `localhost`, not 127.0.0.1: the BFF compares the browser Origin with the URL Next derives from
// its --hostname, which must match for state-changing calls to pass the CSRF origin check.
export const CONSOLE_URL = `http://localhost:${CONSOLE_PORT}`;

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
  use: {
    baseURL: process.env.E2E_BASE_URL ?? `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  ...(process.env.E2E_BASE_URL
    ? {}
    : {
        webServer: [
          {
            command: `node node_modules/next/dist/bin/next start --port ${PORT} --hostname 127.0.0.1`,
            url: `http://127.0.0.1:${PORT}`,
            reuseExistingServer: !process.env.CI,
            timeout: 60_000,
            env: { AQUA_API_URL: "", NEXT_PUBLIC_API_URL: "", FEATURE_DEMO: "" },
          },
          {
            command: `node e2e/fixtures/mock-api.mjs`,
            url: `http://127.0.0.1:${MOCK_PORT}/api/v1/demo/status`,
            reuseExistingServer: !process.env.CI,
            timeout: 30_000,
            env: { MOCK_API_PORT: String(MOCK_PORT) },
          },
          {
            command: `node node_modules/next/dist/bin/next start --port ${CONSOLE_PORT} --hostname localhost`,
            url: CONSOLE_URL,
            reuseExistingServer: !process.env.CI,
            timeout: 60_000,
            env: {
              AQUA_API_URL: `http://127.0.0.1:${MOCK_PORT}`,
              NEXT_PUBLIC_API_URL: "",
              FEATURE_DEMO: "1",
              COOKIE_INSECURE: "1",
            },
          },
        ],
      }),
});
