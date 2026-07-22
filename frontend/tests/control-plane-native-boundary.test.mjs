import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, frontendRoot), "utf8");
}

test("the production network bridge exposes only fixed Control Plane operations", async () => {
  const [cargoManifest, rustEntry, bridgeSource, transportSource, productionMain] =
    await Promise.all([
      readProjectFile("src-tauri/Cargo.toml"),
      readProjectFile("src-tauri/src/lib.rs"),
      readProjectFile("src-tauri/src/control_plane.rs"),
      readProjectFile("src/platform/tauri/control-plane-transport.ts"),
      readProjectFile("src/main.tsx"),
    ]);

  assert.match(cargoManifest, /reqwest\s*=/);
  assert.match(cargoManifest, /serde\s*=/);
  assert.match(rustEntry, /invoke_handler/);
  assert.match(rustEntry, /check_control_plane_health/);
  assert.match(bridgeSource, /ControlPlaneOperation/);
  assert.match(bridgeSource, /GetSystemHealth/);
  assert.match(bridgeSource, /GetCurrentInstallationAccess/);
  assert.match(bridgeSource, /IssueInstallationRegistrationChallenge/);
  assert.match(bridgeSource, /CompleteInstallationRegistration/);
  assert.match(bridgeSource, /RotateDeviceCredential/);
  assert.match(bridgeSource, /RevokeDeviceCredential/);
  assert.match(bridgeSource, /ExchangeDeviceSession/);
  assert.match(bridgeSource, /http:\/\/127\.0\.0\.1:8765/);
  assert.match(bridgeSource, /Policy::none\(\)/);
  assert.match(bridgeSource, /\.no_proxy\(\)/);
  assert.match(bridgeSource, /connect_timeout/);
  assert.match(bridgeSource, /MAX_RESPONSE_LENGTH/);
  assert.match(bridgeSource, /no-store/);
  assert.doesNotMatch(bridgeSource, /pub\s+(?:async\s+)?fn\s+\w+\([^)]*(?:url|uri|path)\s*:/i);
  assert.match(transportSource, /invoke.*check_control_plane_health/s);
  assert.doesNotMatch(transportSource, /fetch\(|axios|baseUrl|https?:\/\//i);
  assert.match(productionMain, /createDesktopStartupCheck/);
  assert.match(productionMain, /TauriControlPlaneTransport/);
  assert.match(productionMain, /TauriStartupEnvironmentGateway/);
});

test("device credentials remain native while the bridge injects them", async () => {
  const [bridgeSource, credentialSource] = await Promise.all([
    readProjectFile("src-tauri/src/control_plane.rs"),
    readProjectFile("src-tauri/src/device_credentials.rs"),
  ]);

  assert.match(bridgeSource, /DeviceCredentialVault/);
  assert.match(bridgeSource, /authorization/i);
  assert.match(bridgeSource, /x-request-id/i);
  assert.doesNotMatch(credentialSource, /tauri::command|Serialize|Deserialize/);
});
