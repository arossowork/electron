import { defineConfig } from "@playwright/test";

// e2e runs against a running stack: COMPOSE_PROFILES=web scripts/platform.sh up (see docs/web.md).
// HWPLATFORM_DEV_AUTO_VERIFY=true is required so the test can sign up without reading an e-mail.
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:8088",
    viewport: { width: 1180, height: 800 },
    trace: "retain-on-failure",
  },
});
