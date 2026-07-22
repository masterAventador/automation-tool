import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);
const frontendRoot = new URL("frontend/", repositoryRoot);

test("H8-18 pins the official Rust updater without exposing its JavaScript commands", async () => {
  const [cargo, cargoLock, packageManifest, capability] = await Promise.all([
    readFile(new URL("src-tauri/Cargo.toml", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/Cargo.lock", frontendRoot), "utf8"),
    readFile(new URL("package.json", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/capabilities/main.json", frontendRoot), "utf8"),
  ]);

  assert.match(cargo, /tauri-plugin-updater\s*=\s*"=2\.10\.1"/u);
  assert.match(cargoLock, /name = "tauri-plugin-updater"\nversion = "2\.10\.1"/u);
  assert.doesNotMatch(packageManifest, /@tauri-apps\/plugin-updater/u);
  assert.doesNotMatch(capability, /updater:/u);
});

test("H8-18 keeps release policy and native state closed and business agnostic", async () => {
  const [nativeContract, webviewContract, architecture] = await Promise.all([
    readFile(new URL("src-tauri/src/app_updates.rs", frontendRoot), "utf8"),
    readFile(new URL("src/features/app-updates/contracts.ts", frontendRoot), "utf8"),
    readFile(new URL("../docs/frontend-architecture.md", frontendRoot), "utf8"),
  ]);

  assert.match(nativeContract, /parse_official_update/u);
  assert.match(nativeContract, /update\.raw_json/u);
  assert.match(nativeContract, /UpdatePolicy[\s\S]*Optional[\s\S]*Forced/u);
  assert.match(nativeContract, /url\.scheme\(\) != "https"/u);
  assert.match(nativeContract, /MAX_UPDATE_ARTIFACT_BYTES/u);
  assert.doesNotMatch(webviewContract, /downloadUrl|\bsignature\s*:/u);
  assert.match(webviewContract, /startup[\s\S]*periodic[\s\S]*manual/u);
  assert.match(webviewContract, /install_now[\s\S]*defer[\s\S]*skip_version/u);
  assert.match(architecture, /tauri-plugin-updater 2\.10\.1/u);
  const nativeProduction = nativeContract.split("#[cfg(test)]", 1)[0];
  for (const forbidden of ["douyin", "taskId", "customerId"]) {
    assert.doesNotMatch(nativeProduction, new RegExp(forbidden, "u"));
    assert.doesNotMatch(webviewContract, new RegExp(forbidden, "u"));
  }
});
