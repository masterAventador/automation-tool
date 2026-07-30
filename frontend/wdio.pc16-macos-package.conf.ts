import { isAbsolute } from "node:path";

import type { TauriCapabilities } from "@wdio/tauri-service";
import { wdioRuntimeArtifacts } from "./wdio-runtime-artifacts";

/**
 * PC-16: the one-sentence film against a *bundled* App.
 *
 * Same spec, same budgets as the T36 runner; the one difference is the App
 * under test — a real `.app` whose resources live in `Contents/Resources`,
 * the layout `resource_dir()` only exercises in a bundle. The binary path
 * comes from the runner because the bundle name and location are the runner's
 * decision, not this file's.
 */
const appBinaryPath = process.env.PC16_MAC_APP_BINARY;
if (appBinaryPath === undefined || !isAbsolute(appBinaryPath)) {
  throw new Error("PC-16 macOS package App binary is unavailable");
}

const capabilities: TauriCapabilities = {
  browserName: "tauri",
  "tauri:options": { application: appBinaryPath },
};

export const config: WebdriverIO.Config = {
  ...wdioRuntimeArtifacts,
  runner: "local",
  specs: ["./e2e-tauri/motion-one-sentence.spec.ts"],
  maxInstances: 1,
  services: [
    [
      "@wdio/tauri-service",
      {
        appBinaryPath,
        driverProvider: "embedded",
        autoInstallTauriDriver: false,
        autoDownloadEdgeDriver: true,
        captureBackendLogs: true,
        captureFrontendLogs: true,
        startTimeout: 60_000,
        commandTimeout: 90_000,
      },
    ],
  ],
  capabilities: [capabilities],
  logLevel: "warn",
  bail: 1,
  waitforTimeout: 30_000,
  connectionRetryTimeout: 120_000,
  connectionRetryCount: 1,
  framework: "mocha",
  reporters: ["spec"],
  mochaOpts: { ui: "bdd", timeout: 1_800_000 },
};
