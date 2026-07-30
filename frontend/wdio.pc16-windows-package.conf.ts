import { isAbsolute } from "node:path";

import type { TauriCapabilities } from "@wdio/tauri-service";
import { wdioRuntimeArtifacts } from "./wdio-runtime-artifacts";

/**
 * PC-16: drive the App binary from an installed Windows NSIS package.
 *
 * The runner owns the isolated product identity and supplies the absolute
 * LocalAppData install path. Resource resolution therefore goes through the
 * same installed-root layout as a customer package, never target/debug.
 */
const appBinaryPath = process.env.PC16_WINDOWS_APP_BINARY;
if (appBinaryPath === undefined || !isAbsolute(appBinaryPath)) {
  throw new Error("PC-16 Windows package App binary is unavailable");
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
