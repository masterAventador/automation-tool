import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, frontendRoot), "utf8");
}

test("Control Plane acceptance has an isolated real-Tauri build path", async () => {
  const [
    manifest,
    packageJson,
    tauriConfig,
    viteConfig,
    wdioConfig,
    testEntrypoint,
  ] =
    await Promise.all([
      readProjectFile("src-tauri/Cargo.toml"),
      readProjectFile("package.json"),
      readProjectFile("src-tauri/tauri.control-plane-e2e.conf.json"),
      readProjectFile("vite.config.ts"),
      readProjectFile("wdio.control-plane.conf.ts"),
      readProjectFile("src/test-control-plane-main.ts"),
    ]);

  assert.match(manifest, /control-plane-e2e\s*=\s*\["desktop-test-driver"\]/);
  assert.match(manifest, /desktop-e2e\s*=\s*\["desktop-test-driver"\]/);
  assert.match(packageJson, /build:control-plane-e2e-assets/);
  assert.match(packageJson, /test:control-plane-tauri/);
  assert.match(tauriConfig, /com\.aventador\.automationtool\.i209acceptance/);
  assert.match(tauriConfig, /build:control-plane-e2e-assets/);
  assert.match(tauriConfig, /withGlobalTauri/);
  assert.match(tauriConfig, /"visible"\s*:\s*false/);
  assert.match(viteConfig, /control-plane-e2e/);
  assert.match(viteConfig, /test-control-plane-main\.ts/);
  assert.match(wdioConfig, /control-plane\.spec\.ts/);
  assert.doesNotMatch(wdioConfig, /e2e-tauri\/\*\*/);
  assert.match(testEntrypoint, /@wdio\/tauri-plugin/);
  assert.match(testEntrypoint, /\.\/main/);
  assert.doesNotMatch(testEntrypoint, /createRoot|<App|checkHealth/);
});

test("the acceptance command exercises the production client without returning secrets", async () => {
  const [rustEntry, acceptanceSpec] = await Promise.all([
    readProjectFile("src-tauri/src/lib.rs"),
    readProjectFile("e2e-tauri/control-plane.spec.ts"),
  ]);

  assert.match(rustEntry, /cfg\(feature = "control-plane-e2e"\)/);
  assert.match(rustEntry, /run_control_plane_acceptance/);
  assert.match(rustEntry, /register_installation/);
  assert.match(rustEntry, /exchange_device_session/);
  assert.match(rustEntry, /rotate_device_credential/);
  assert.match(rustEntry, /revoke_device_credential/);
  assert.match(rustEntry, /ProductionDeviceIdentity/);
  assert.match(rustEntry, /ProductionDeviceCredentialVault/);
  assert.match(acceptanceSpec, /run_control_plane_acceptance/);
  assert.match(acceptanceSpec, /RPA 运营工作台/);
  assert.doesNotMatch(acceptanceSpec, /credential|sessionToken|bootstrapToken/i);
  assert.doesNotMatch(
    rustEntry,
    /struct\s+ControlPlaneAcceptanceSummary\s*\{[^}]*(?:credential|token)\s*:/is,
  );
});

test("the real-backend orchestrator isolates PostgreSQL and App data then verifies final state", async () => {
  const orchestrator = await readFile(
    new URL("../../scripts/run_i2_09_acceptance.py", import.meta.url),
    "utf8",
  );

  assert.match(orchestrator, /docker[\s\S]*compose/);
  assert.match(orchestrator, /postgres-test/);
  assert.match(orchestrator, /alembic/);
  assert.match(orchestrator, /uvicorn/);
  assert.match(orchestrator, /pnpm[\s\S]*test:control-plane-tauri/);
  assert.match(orchestrator, /com\.aventador\.automationtool\.i209acceptance/);
  assert.match(orchestrator, /device-identity-ed25519-v1/);
  assert.match(orchestrator, /device-credential-v1/);
  assert.match(orchestrator, /device_sessions/);
  assert.match(orchestrator, /shutil\.rmtree/);
  assert.doesNotMatch(orchestrator, /print\([^\n]*(?:token|password|secret)/i);
});
