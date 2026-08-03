import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

test("desktop-e2e mounts the production composition through its controlled native adapter", async () => {
  const [viteConfig, desktopEntry, productionEntry] = await Promise.all([
    readFile(new URL("vite.config.ts", frontendRoot), "utf8"),
    readFile(new URL("src/test-tauri-main.tsx", frontendRoot), "utf8"),
    readFile(new URL("src/main.tsx", frontendRoot), "utf8"),
  ]);

  assert.match(
    viteConfig,
    /desktopE2EMode[\s\S]*test-tauri-main\.tsx/u,
    "desktop-e2e must keep its WebdriverIO-capable entry",
  );
  assert.match(desktopEntry, /import\s+"@wdio\/tauri-plugin"/u);
  assert.match(
    desktopEntry,
    /void\s+import\("\.\/main"\)/u,
    "the desktop test entry must execute the production composition root",
  );
  assert.doesNotMatch(desktopEntry, /desktopShellStartupCheck/u);
  assert.doesNotMatch(desktopEntry, /<App\b/u);

  for (const productionDependency of [
    "createDesktopStartupCheck",
    "TauriControlPlaneTransport",
    "TauriStartupEnvironmentGateway",
    "TauriTaskProjectionSource",
    "TauriTaskCreationGateway",
    "TauriTaskRunControlGateway",
    "TauriTaskDiscoveryGateway",
    "TauriTaskTargetPreviewSource",
    "TauriTaskTargetResultSource",
    "TauriWorkbenchGateway",
    "TauriPlatformAdapter",
    "TauriPlatformSessionGateway",
    "TauriAppUpdateGateway",
    "TauriAccountSessionGateway",
    "TauriModelServiceGateway",
    "TauriVideoEditingGateway",
    "TauriMaterialVideoStudioGateway",
    "TauriPublishWorkspaceGateway",
  ]) {
    assert.match(
      productionEntry,
      new RegExp(`new ${productionDependency}\\b|${productionDependency}\\(`, "u"),
      `${productionDependency} is absent from the production composition root`,
    );
  }
});
