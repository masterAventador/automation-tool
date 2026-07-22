import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

async function read(path) {
  return readFile(new URL(path, root), "utf8");
}

test("C10-07 keeps the signed Demo Profile inside the native trust boundary", async () => {
  const [profile, build, controlPlane, native] = await Promise.all([
    read("src-tauri/src/deployment_profile.rs"),
    read("src-tauri/build.rs"),
    read("src-tauri/src/control_plane.rs"),
    read("src-tauri/src/lib.rs"),
  ]);

  assert.match(profile, /customer-demo-profile\.v1/u);
  assert.match(profile, /verify_strict/u);
  assert.match(profile, /AUTOMATION_TOOL_COMPILED_DEPLOYMENT_PROFILE_PAYLOAD/u);
  assert.match(profile, /https/u);
  assert.match(profile, /allowed_hosts/u);
  assert.match(profile, /profiles/u);
  assert.doesNotMatch(profile, /std::env::var/u);
  assert.match(build, /validate_optional_deployment_profile/u);
  assert.match(build, /cargo:rustc-env=AUTOMATION_TOOL_COMPILED_DEPLOYMENT_PROFILE_/u);
  assert.match(controlPlane, /for_deployment_profile/u);
  assert.match(controlPlane, /wss:\/\//u);
  assert.match(native, /prepare_data_directory/u);
});

test("C10-07 owns an executable compiled-profile and isolation acceptance", async () => {
  const [acceptance, packageSource, accountVault, credentials, identity] = await Promise.all([
    read("../scripts/run_c10_07_acceptance.py"),
    read("package.json"),
    read("src-tauri/src/account_session_vault.rs"),
    read("src-tauri/src/device_credentials.rs"),
    read("src-tauri/src/device_identity.rs"),
  ]);

  for (const marker of [
    "AUTOMATION_TOOL_DEPLOYMENT_PROFILE_PAYLOAD",
    "AUTOMATION_TOOL_DEPLOYMENT_PROFILE_SIGNATURE",
    "AUTOMATION_TOOL_DEPLOYMENT_PROFILE_VERIFYING_KEY",
    "api.automation-tool.test",
    "compiled_profile_matches_build_contract",
  ]) {
    assert.match(acceptance, new RegExp(marker, "u"));
  }
  assert.match(packageSource, /test:c10-07-profile/u);
  assert.match(accountVault, /product-account-session-v1/u);
  assert.match(credentials, /device-credential-v1/u);
  assert.match(identity, /device-identity-ed25519-v1/u);
});
