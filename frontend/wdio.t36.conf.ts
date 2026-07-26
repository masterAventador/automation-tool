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

/**
 * The one-sentence acceptance gets its own runner configuration for one reason
 * the shared video-studio configuration cannot give it: time.
 *
 * This spec waits on a real model round trip and then a 360 frame render in a
 * real browser. Both are minutes long, and the shared configuration's three
 * minute Mocha budget cut the run off mid-render with a bare "Timeout" that
 * said nothing about which step was still going. Raising the shared budget
 * would slow every other video-studio spec's failures to the same 30 minutes.
 */
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
