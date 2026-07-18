import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, frontendRoot), "utf8");
}

test("Installation revocation has one isolated hidden real-Tauri acceptance path", async () => {
  const [packageJson, tauriConfig, wdioConfig, acceptanceSpec] = await Promise.all([
    readProjectFile("package.json"),
    readProjectFile("src-tauri/tauri.installation-revocation-e2e.conf.json"),
    readProjectFile("wdio.installation-revocation.conf.ts"),
    readProjectFile("e2e-tauri/installation-revocation.spec.ts"),
  ]);

  assert.match(packageJson, /test:installation-revocation-tauri/);
  assert.match(packageJson, /build:tauri:installation-revocation-test/);
  assert.match(tauriConfig, /com\.aventador\.automationtool\.i214acceptance/);
  assert.match(tauriConfig, /"visible"\s*:\s*false/);
  assert.match(wdioConfig, /installation-revocation\.spec\.ts/);
  assert.doesNotMatch(wdioConfig, /e2e-tauri\/\*\*/);
  assert.match(acceptanceSpec, /register_installation_for_revocation_acceptance/);
  assert.match(acceptanceSpec, /当前安装实例已失效/);
  assert.doesNotMatch(acceptanceSpec, /credential|sessionToken|bootstrapToken/i);
});

test("the native acceptance registration returns no secret and checks App access", async () => {
  const rustEntry = await readProjectFile("src-tauri/src/lib.rs");

  assert.match(rustEntry, /register_installation_for_revocation_acceptance/);
  assert.match(rustEntry, /check_installation_access_if_registered/);
  assert.match(rustEntry, /AUTOMATION_TOOL_I214_BOOTSTRAP_TOKEN/);
  assert.doesNotMatch(
    rustEntry,
    /struct\s+InstallationRevocationAcceptanceRegistration\s*\{[^}]*(?:credential|token)\s*:/is,
  );
});

test("the orchestrator uses the operator CLI and cleans isolated App and database state", async () => {
  const orchestrator = await readFile(
    new URL("../../scripts/run_i2_14_acceptance.py", import.meta.url),
    "utf8",
  );

  assert.match(orchestrator, /automation-tool-revoke-installation/);
  assert.match(orchestrator, /test:installation-revocation-tauri/);
  assert.match(orchestrator, /require_hidden_tauri_configuration/);
  assert.match(orchestrator, /postgres-test/);
  assert.match(orchestrator, /device_sessions/);
  assert.match(orchestrator, /device-credential-v1/);
  assert.match(orchestrator, /shutil\.rmtree/);
  assert.doesNotMatch(orchestrator, /print\([^\n]*(?:token|password|secret)/i);
});
