import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, frontendRoot), "utf8");
}

test("the four desktop test layers have explicit commands", async () => {
  const packageJson = JSON.parse(await readProjectFile("package.json"));

  assert.equal(packageJson.scripts["test:unit"], "vitest run");
  assert.equal(packageJson.scripts["test:ui"], "playwright test");
  assert.match(packageJson.scripts["test:rust"], /^cargo test /);
  assert.match(packageJson.scripts["test:tauri"], /wdio run wdio\.conf\.ts/);
  assert.equal(
    packageJson.scripts["test:layers"],
    "pnpm test && pnpm test:ui && pnpm test:rust && pnpm test:tauri",
  );
});

test("WebdriverIO uses the embedded driver against a real Tauri binary", async () => {
  const [wdioConfig, desktopSpec] = await Promise.all([
    readProjectFile("wdio.conf.ts"),
    readProjectFile("e2e-tauri/workbench.spec.ts"),
  ]);

  assert.match(wdioConfig, /@wdio\/tauri-service/);
  assert.match(wdioConfig, /driverProvider:\s*["']embedded["']/);
  assert.match(wdioConfig, /autoDownloadEdgeDriver:\s*true/);
  assert.match(wdioConfig, /src-tauri[\\/]target[\\/]debug/);
  assert.match(wdioConfig, /browserName:\s*["']tauri["']/);
  assert.match(wdioConfig, /e2e-tauri\/workbench\.spec\.ts/);
  assert.doesNotMatch(wdioConfig, /e2e-tauri\/\*\*|control-plane\.spec\.ts/);
  assert.match(desktopSpec, /RPA 运营工作台/);
  assert.match(desktopSpec, /listWindows/);
});

test("WDIO plugins and permissions are isolated from production", async () => {
  const [cargoManifest, rustEntry, testConfigText, productionConfigText] = await Promise.all([
    readProjectFile("src-tauri/Cargo.toml"),
    readProjectFile("src-tauri/src/lib.rs"),
    readProjectFile("src-tauri/tauri.test.conf.json"),
    readProjectFile("src-tauri/tauri.conf.json"),
  ]);
  const testConfig = JSON.parse(testConfigText);
  const productionConfig = JSON.parse(productionConfigText);
  const [mainCapability, testCapability] = testConfig.app.security.capabilities;

  assert.match(cargoManifest, /\[features\][\s\S]*desktop-e2e/);
  assert.match(cargoManifest, /tauri-plugin-wdio/);
  assert.match(cargoManifest, /tauri-plugin-wdio-webdriver/);
  assert.match(rustEntry, /cfg\(feature = "desktop-e2e"\)/);
  assert.equal(mainCapability, "main");
  assert.equal(testCapability.identifier, "wdio");
  assert.equal(testConfig.app.withGlobalTauri, true);
  assert.deepEqual(testConfig.app.windows, [{ label: "main", visible: false }]);
  assert.ok(testCapability.permissions.includes("wdio:allow-list-windows"));
  assert.ok(testCapability.permissions.includes("wdio-webdriver:default"));
  await assert.rejects(readProjectFile("src-tauri/capabilities/wdio.json"), { code: "ENOENT" });
  assert.deepEqual(productionConfig.app.security.capabilities, ["main"]);
  assert.equal(productionConfig.app.withGlobalTauri, false);
});
