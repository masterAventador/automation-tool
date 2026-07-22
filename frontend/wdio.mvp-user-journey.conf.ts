import { resolve } from "node:path";

import type { TauriCapabilities } from "@wdio/tauri-service";
import { wdioRuntimeArtifacts } from "./wdio-runtime-artifacts";

const binaryName =
  process.platform === "win32" ? "automation-tool-desktop.exe" : "automation-tool-desktop";
const appBinaryPath = resolve("src-tauri/target/debug", binaryName);
const capabilities: TauriCapabilities = {
  browserName: "tauri",
  "tauri:options": { application: appBinaryPath },
};

export const config: WebdriverIO.Config = {
  ...wdioRuntimeArtifacts,
  runner: "local",
  specs: ["./e2e-tauri/mvp-user-journey.spec.ts"],
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
        commandTimeout: 120_000,
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
  mochaOpts: { ui: "bdd", timeout: 360_000 },
};
