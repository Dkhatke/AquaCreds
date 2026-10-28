import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.E2E_PORT ?? 3100);

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
        webServer: {
          command: `node node_modules/next/dist/bin/next start --port ${PORT} --hostname 127.0.0.1`,
          url: `http://127.0.0.1:${PORT}`,
          reuseExistingServer: !process.env.CI,
          timeout: 60_000,
          env: { AQUA_API_URL: "", NEXT_PUBLIC_API_URL: "", FEATURE_DEMO: "" },
        },
      }),
});
