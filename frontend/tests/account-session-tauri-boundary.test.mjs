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
  assert.match(main, /MODE === "customer-demo"/u);
  assert.match(packageSource, /build:customer-demo-assets/u);
  assert.match(packageSource, /test:account-session-tauri/u);
  assert.match(tauriConfig, /"visible": false/u);

  const webview = `${app}\n${adapter}`;
  assert.doesNotMatch(webview, /accessToken|refreshToken|localStorage|sessionStorage/u);
  assert.doesNotMatch(native, /system.*keychain|keyring|credential manager/iu);
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
