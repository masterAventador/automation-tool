import { isAbsolute } from "node:path";

import type { TauriCapabilities } from "@wdio/tauri-service";
import { wdioRuntimeArtifacts } from "./wdio-runtime-artifacts";

const appBinaryPath = process.env.H822_WINDOWS_APP_BINARY;
if (appBinaryPath === undefined || !isAbsolute(appBinaryPath)) {
  throw new Error("H8-22 Windows package App binary is unavailable");
}

const capabilities: TauriCapabilities = {
  browserName: "tauri",
  "tauri:options": { application: appBinaryPath },
};

export const config: WebdriverIO.Config = {
  ...wdioRuntimeArtifacts,
  runner: "local",
  specs: ["./e2e-tauri/update-windows-package.spec.ts"],
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
        commandTimeout: 30_000,
      },
    ],
  ],
  capabilities: [capabilities],
  logLevel: "warn",
  bail: 1,
  waitforTimeout: 30_000,
  connectionRetryTimeout: 90_000,
  connectionRetryCount: 1,
  framework: "mocha",
  reporters: ["spec"],
  mochaOpts: { ui: "bdd", timeout: 120_000 },
};
