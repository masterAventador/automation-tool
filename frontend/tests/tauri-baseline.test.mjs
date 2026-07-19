import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, frontendRoot), "utf8");
}

test("Tauri v2 is the only desktop runtime entry", async () => {
  const packageJson = JSON.parse(await readProjectFile("package.json"));
  const cargoManifest = await readProjectFile("src-tauri/Cargo.toml");

  assert.equal(packageJson.scripts.tauri, "tauri");
  assert.match(packageJson.devDependencies["@tauri-apps/cli"], /^\^?2\./);
  assert.match(cargoManifest, /tauri\s*=\s*\{\s*version\s*=\s*"2/);
  assert.match(cargoManifest, /tauri-build\s*=\s*\{\s*version\s*=\s*"2/);
});

test("desktop window consumes only loopback Vite or bundled assets", async () => {
  const packageJson = JSON.parse(await readProjectFile("package.json"));
  const config = JSON.parse(await readProjectFile("src-tauri/tauri.conf.json"));
  const devConfig = JSON.parse(await readProjectFile("src-tauri/tauri.dev.conf.json"));

  assert.equal(config.build.devUrl, undefined);
  assert.equal(config.build.beforeDevCommand, undefined);
  assert.equal(config.app.security.devCsp, undefined);
  assert.equal(config.build.frontendDist, "../dist");
  assert.equal(config.build.beforeBuildCommand, "pnpm build");
  assert.equal(devConfig.build.devUrl, "http://127.0.0.1:1420");
  assert.equal(devConfig.build.beforeDevCommand, "pnpm dev");
  assert.match(devConfig.app.security.devCsp["connect-src"], /127\.0\.0\.1:1420/);
  assert.equal(packageJson.scripts["tauri:dev"], "tauri dev --config src-tauri/tauri.dev.conf.json");
  assert.deepEqual(config.app.windows, [
    {
      label: "main",
      title: "自动化运营工具",
      width: 1280,
      height: 800,
      minWidth: 960,
      minHeight: 640,
      center: true,
      resizable: true,
      fullscreen: false,
    },
  ]);
  assert.equal(config.app.withGlobalTauri, false);
  assert.equal(config.app.windows[0].url, undefined);
});

test("main window has an explicit least-privilege capability and production CSP", async () => {
  const [configText, capabilityText] = await Promise.all([
    readProjectFile("src-tauri/tauri.conf.json"),
    readProjectFile("src-tauri/capabilities/main.json"),
  ]);
  const config = JSON.parse(configText);
  const capability = JSON.parse(capabilityText);

  assert.deepEqual(config.app.security.capabilities, ["main"]);
  assert.equal(config.app.security.dangerousDisableAssetCspModification, false);
  assert.equal(config.app.security.csp["default-src"], "'self'");
  assert.equal(config.app.security.csp["connect-src"], "ipc: http://ipc.localhost");
  assert.deepEqual(capability.windows, ["main"]);
  assert.deepEqual(capability.permissions, []);
  assert.equal(capability.remote, undefined);
});
