import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.E2E_PORT ?? 3100);

/**
 * Smoke tests against the production build (`pnpm build` first). They need no API: pages that
 * depend on it must degrade honestly (hidden counts, "demo not available"), which is part of what
 * is tested. Set E2E_BASE_URL to test a running deployment instead.
 */
const MOCK_PORT = Number(process.env.MOCK_API_PORT ?? 4721);
const MOCKED_PORT = Number(process.env.E2E_MOCKED_PORT ?? 3721);
const nextStart = (port: number) =>
  `node node_modules/next/dist/bin/next start --port ${port} --hostname 127.0.0.1`;

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
      // Runs against a second server wired to e2e/mock-api.mjs (mocked API responses).
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
            command: "node e2e/mock-api.mjs",
            url: `http://127.0.0.1:${MOCK_PORT}/api/v1/public/vintages`,
            reuseExistingServer: false,
            env: { MOCK_API_PORT: String(MOCK_PORT) },
          },
          {
            command: nextStart(MOCKED_PORT),
            url: `http://127.0.0.1:${MOCKED_PORT}`,
            reuseExistingServer: false,
            timeout: 60_000,
            env: {
              AQUA_API_URL: `http://127.0.0.1:${MOCK_PORT}`,
              NEXT_PUBLIC_API_URL: "",
              FEATURE_DEMO: "",
            },
          },
        ],
      }),
});
