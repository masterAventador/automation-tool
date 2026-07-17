import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, frontendRoot), "utf8");
}

async function readFrontendSources(directory = new URL("src/", frontendRoot)) {
  const entries = await readdir(directory, { withFileTypes: true });
  const sources = [];
  for (const entry of entries) {
    const url = new URL(entry.name + (entry.isDirectory() ? "/" : ""), directory);
    if (entry.isDirectory()) {
      sources.push(...(await readFrontendSources(url)));
    } else if (/\.[cm]?[jt]sx?$/.test(entry.name)) {
      sources.push(await readFile(url, "utf8"));
    }
  }
  return sources;
}

test("device identity uses Ed25519 and native secure stores at the Rust boundary", async () => {
  const [cargoManifest, rustEntry, identitySource] = await Promise.all([
    readProjectFile("src-tauri/Cargo.toml"),
    readProjectFile("src-tauri/src/lib.rs"),
    readProjectFile("src-tauri/src/device_identity.rs"),
  ]);

  assert.match(cargoManifest, /rust-version\s*=\s*"1\.88"/);
  assert.match(cargoManifest, /ed25519-dalek\s*=\s*"=3\.0\.0"/);
  assert.match(cargoManifest, /getrandom\s*=\s*"=0\.4\.3"/);
  assert.match(cargoManifest, /zeroize\s*=\s*"=1\.9\.0"/);
  assert.match(cargoManifest, /keyring\s*=\s*"=4\.1\.5"/);
  assert.match(rustEntry, /mod device_identity;/);
  assert.match(rustEntry, /initialize_production_identity/);
  assert.match(rustEntry, /initialize_ephemeral_identity/);
  assert.doesNotMatch(rustEntry, /invoke_handler/);
  assert.match(identitySource, /SigningKey/);
  assert.match(identitySource, /getrandom::fill/);
  assert.match(identitySource, /Zeroizing/);
  assert.match(identitySource, /KeyringDeviceSecretStore/);
  assert.match(identitySource, /real_platform_secure_store_round_trip/);
  assert.doesNotMatch(identitySource, /std::fs|File::create|tauri::command|Serialize|Deserialize/);
});

test("React sources have no device private-key or secure-store surface", async () => {
  const sourceText = (await readFrontendSources()).join("\n");

  assert.doesNotMatch(
    sourceText,
    /private[_-]?key|signing[_-]?key|device[_-]?secret|keyring|credential manager/i,
  );
});
