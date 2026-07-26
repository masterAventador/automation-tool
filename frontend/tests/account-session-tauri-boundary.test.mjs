import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

async function read(path) {
  return readFile(new URL(path, root), "utf8");
}

test("U9-04 keeps account bearer secrets in one Rust vault behind fixed Commands", async () => {
  const [vault, controlPlane, native, app, adapter, main, packageSource, tauriConfig] = await Promise.all([
    read("src-tauri/src/account_session_vault.rs"),
    read("src-tauri/src/control_plane.rs"),
    read("src-tauri/src/lib.rs"),
    read("src/app/App.tsx"),
    read("src/platform/tauri/account-session-gateway.ts"),
    read("src/main.tsx"),
    read("package.json"),
    read("src-tauri/tauri.account-session-e2e.conf.json"),
  ]);

  for (const operation of [
    "LoginAccountSession",
    "RefreshAccountSession",
    "LogoutAccountSession",
    "ChangeAccountPassword",
    "RecoverAccountPassword",
  ]) {
    assert.match(controlPlane, new RegExp(`\\b${operation}\\b`, "u"));
  }
  assert.match(vault, /product-account-session-v1/u);
  assert.match(vault, /AppDataSecretStore/u);
  assert.match(native, /restore_product_account_session/u);
  assert.match(native, /login_product_account/u);
  assert.match(native, /recover_product_account_password/u);
  assert.match(native, /change_product_account_password/u);
  assert.match(native, /logout_product_account/u);
  assert.match(app, /AccountSessionGate/u);
  assert.match(adapter, /parseAccountSessionSnapshot/u);
  assert.match(packageSource, /test:account-session-tauri/u);
  assert.match(tauriConfig, /"visible": false/u);

  // The login screen must exist in every build and be mounted on the strength
  // of a runtime answer. It used to be mounted only when Vite compiled the
  // bundle in `customer-demo` mode, so the release package — built in the
  // default mode — had no login screen in it at all, and no gate anywhere said
  // so. A build mode may select a configuration value; it may not decide
  // whether a product capability exists.
  assert.match(main, /new TauriAccountSessionGateway\(\)/u);
  assert.doesNotMatch(main, /import\.meta\.env/u);
  assert.doesNotMatch(packageSource, /customer-demo-assets/u);
  assert.match(tauriConfig, /"beforeBuildCommand": "pnpm build"/u);

  const webview = `${app}\n${adapter}`;
  assert.doesNotMatch(webview, /accessToken|refreshToken|localStorage|sessionStorage/u);
  assert.doesNotMatch(native, /system.*keychain|keyring|credential manager/iu);
});

test("the deployment configuration, not the build mode, decides whether login is required", async () => {
  const [profile, native, gate, contract, packageSource] = await Promise.all([
    read("src-tauri/src/deployment_profile.rs"),
    read("src-tauri/src/lib.rs"),
    read("src/features/account-session/AccountSessionGate.tsx"),
    read("src/features/account-session/account-session-gateway.ts"),
    read("package.json"),
  ]);

  // One read site, compiled into every binary. A local build may be pointed at
  // an isolated instance that does issue product accounts (U9-04, U9-06); the
  // switch can only ever *add* the requirement, never remove one, so a build
  // that forgets it fails closed with a login screen rather than open.
  assert.match(profile, /fn requires_product_account/u);
  assert.match(profile, /AUTOMATION_TOOL_ISOLATED_PRODUCT_ACCOUNT_INSTANCE/u);
  assert.match(profile, /option_env!/u);
  assert.doesNotMatch(profile, /std::env::var/u);
  assert.match(native, /requires_product_account/u);

  // The runtime answer travels as a third snapshot state, so the one gateway
  // and the one component serve every deployment.
  assert.match(contract, /not_required/u);
  assert.match(gate, /not_required/u);

  // The Vite mode that used to carry the login screen is gone, not left behind
  // as a second way to build the same assets.
  assert.doesNotMatch(packageSource, /customer-demo/u);
});

test("U9-05 binds the native device before publishing an authenticated snapshot", async () => {
  const [controlPlane, native] = await Promise.all([
    read("src-tauri/src/control_plane.rs"),
    read("src-tauri/src/lib.rs"),
  ]);

  for (const operation of [
    "IssueAccountInstallationBindingChallenge",
    "CompleteAccountInstallationBinding",
  ]) {
    assert.match(controlPlane, new RegExp(`\\b${operation}\\b`, "u"));
  }
  assert.match(controlPlane, /account\.installation\.bind|bind_account_installation/u);
  assert.doesNotMatch(controlPlane, /pairing.?code|approval.?poll/iu);

  const loginStart = native.indexOf("async fn login_product_account");
  const loginEnd = native.indexOf("async fn recover_product_account_password", loginStart);
  const login = native.slice(loginStart, loginEnd);
  assert.ok(loginStart >= 0 && loginEnd > loginStart);
  assert.ok(login.indexOf("bind_account_installation") < login.indexOf("vault.replace(&session)"));
  assert.match(login, /ProductionDeviceIdentity/u);
  assert.match(login, /ProductionDeviceCredentialVault/u);
  assert.doesNotMatch(login, /pairing|poll|approval/iu);
});

test("U9-06 keeps owned-device management behind the Rust account Session", async () => {
  const [controlPlane, native, adapter] = await Promise.all([
    read("src-tauri/src/control_plane.rs"),
    read("src-tauri/src/lib.rs"),
    read("src/platform/tauri/account-session-gateway.ts"),
  ]);

  for (const operation of ["ListAccountInstallations", "RevokeAccountInstallation"]) {
    assert.match(controlPlane, new RegExp(`\\b${operation}\\b`, "u"));
  }
  assert.match(native, /list_product_account_devices/u);
  assert.match(native, /revoke_product_account_device/u);
  assert.match(adapter, /parseAccountDevices|parseAccountDevice/u);
  assert.doesNotMatch(adapter, /accessToken|refreshToken|deviceCredential|devicePublicKey/u);
  assert.doesNotMatch(native, /user_id:\s*String|account_id:\s*String/u);
});

test("U9-06 owns one isolated hidden longitudinal Tauri acceptance", async () => {
  const [configurationSource, packageSource, orchestrator, specification] = await Promise.all([
    read("src-tauri/tauri.account-management-e2e.conf.json"),
    read("package.json"),
    read("../scripts/run_u9_06_acceptance.py"),
    read("e2e-tauri/account-management.spec.ts"),
  ]);
  const configuration = JSON.parse(configurationSource);
  assert.equal(configuration.identifier, "com.aventador.automationtool.u906acceptance");
  assert.equal(configuration.app.windows.length, 1);
  assert.equal(configuration.app.windows[0].visible, false);
  assert.match(packageSource, /test:u9-06-tauri/u);
  for (const phase of [
    "login",
    "restart",
    "offline",
    "session-invalid",
    "disabled",
    "device-revoke",
  ]) {
    assert.match(orchestrator, new RegExp(`"${phase}"`, "u"));
    assert.match(specification, new RegExp(`"${phase}"`, "u"));
  }
  assert.match(orchestrator, /verify_final_state|account_operation/u);
  assert.doesNotMatch(orchestrator, /--password|--capability/u);
});
