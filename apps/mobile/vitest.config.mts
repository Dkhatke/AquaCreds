import { defineConfig } from "vitest/config";

// Unit tests run in Node: domain logic, the outbox (against node:sqlite) and the sync engine
// (against the real @aquacreds/api-client with a mocked fetch). Expo native modules are mocked.
export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
    environment: "node",
  },
});
