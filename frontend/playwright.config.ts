import { defineConfig, devices } from "@playwright/test";
import { PRODUCTION_WINDOW } from "./e2e/production-window";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: true,
  retries: 0,
  reporter: [["list"]],
  outputDir: "test-results/playwright",
  use: {
    baseURL: "http://127.0.0.1:1420",
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      /**
       * `viewport` has to come after the preset spread, and has to live here
       * rather than in the top-level `use`.
       *
       * A project-level `use` wins over the top-level one wholesale, and
       * `devices["Desktop Chrome"]` carries its own `viewport` of 1280x720. So
       * a top-level `viewport` is silently discarded while still reading like
       * the setting in force — which is exactly what happened up to T96, and
       * `e2e/viewport-baseline.spec.ts` is the assertion that now catches it.
       *
       * The size itself is read from `src-tauri/tauri.conf.json` so that the
       * fold every spec measures against cannot drift from the window the
       * product opens at.
       */
      use: { ...devices["Desktop Chrome"], viewport: PRODUCTION_WINDOW },
    },
  ],
  webServer: {
    command: "pnpm dev",
    url: "http://127.0.0.1:1420/harness.html",
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
