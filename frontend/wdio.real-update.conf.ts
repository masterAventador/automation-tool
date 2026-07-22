import { isAbsolute, resolve } from "node:path";

import type { TauriCapabilities } from "@wdio/tauri-service";
import { wdioRuntimeArtifacts } from "./wdio-runtime-artifacts";

const configuredBinary = process.env.H822_REAL_APP_BINARY;
if (configuredBinary === undefined || !isAbsolute(configuredBinary)) {
  throw new Error("H8-22 real updater App binary is not an absolute isolated path");
}
const appBinaryPath = resolve(configuredBinary);
const capabilities: TauriCapabilities = {
  browserName: "tauri",
  "tauri:options": { application: appBinaryPath },
};

export const config: WebdriverIO.Config = {
  ...wdioRuntimeArtifacts,
  runner: "local",
  specs: ["./e2e-tauri/real-update.spec.ts"],
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
        startTimeout: 90_000,
        commandTimeout: 90_000,
      },
    ],
  ],
  capabilities: [capabilities],
  logLevel: "warn",
  bail: 1,
  waitforTimeout: 60_000,
  connectionRetryTimeout: 180_000,
  connectionRetryCount: 1,
  framework: "mocha",
  reporters: ["spec"],
  mochaOpts: { ui: "bdd", timeout: 240_000 },
};
