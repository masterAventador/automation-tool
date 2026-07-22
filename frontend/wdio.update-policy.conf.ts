import { rmSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";

import type { TauriCapabilities } from "@wdio/tauri-service";
import { wdioRuntimeArtifacts } from "./wdio-runtime-artifacts";

const acceptanceIdentifier = "com.aventador.automationtool.h819acceptance";
const binaryName =
  process.platform === "win32" ? "automation-tool-desktop.exe" : "automation-tool-desktop";
const appBinaryPath = resolve("src-tauri/target/debug", binaryName);
const capabilities: TauriCapabilities = {
  browserName: "tauri",
  "tauri:options": { application: appBinaryPath },
};

function acceptanceAppDataDirectory(): string {
  if (process.platform === "darwin") {
    return join(homedir(), "Library", "Application Support", acceptanceIdentifier);
  }
  if (process.platform === "win32") {
    const roaming = process.env.APPDATA;
    if (roaming === undefined || roaming.length === 0) {
      throw new Error("Windows roaming AppData is unavailable");
    }
    return join(roaming, acceptanceIdentifier);
  }
  return join(homedir(), ".local", "share", acceptanceIdentifier);
}

function removeAcceptanceAppData(): void {
  rmSync(acceptanceAppDataDirectory(), {
    force: true,
    maxRetries: 3,
    recursive: true,
    retryDelay: 50,
  });
}

export const config: WebdriverIO.Config = {
  ...wdioRuntimeArtifacts,
  runner: "local",
  specs: ["./e2e-tauri/update-policy.spec.ts"],
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
  waitforTimeout: 10_000,
  connectionRetryTimeout: 90_000,
  connectionRetryCount: 1,
  framework: "mocha",
  reporters: ["spec"],
  mochaOpts: { ui: "bdd", timeout: 60_000 },
  onPrepare: removeAcceptanceAppData,
  onComplete: removeAcceptanceAppData,
};
