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

test("device credentials stay in a fixed App-private Rust storage boundary", async () => {
  const [rustEntry, credentialSource, secureStoreSource] = await Promise.all([
    readProjectFile("src-tauri/src/lib.rs"),
    readProjectFile("src-tauri/src/device_credentials.rs"),
    readProjectFile("src-tauri/src/secure_store.rs"),
  ]);

  assert.match(rustEntry, /mod device_credentials;/);
  assert.match(rustEntry, /mod secure_store;/);
  assert.match(rustEntry, /initialize_production_device_credential_vault/);
  assert.match(rustEntry, /app_data_dir/);
  assert.match(credentialSource, /DeviceCredentialVault/);
  assert.match(credentialSource, /Zeroizing/);
  assert.match(credentialSource, /atdc1/);
  assert.match(credentialSource, /real_app_data_device_credential_round_trip/);
  assert.match(secureStoreSource, /AppDataSecretStore/);
  assert.match(secureStoreSource, /OpenOptions/);
  assert.match(secureStoreSource, /create_new/);
  assert.match(secureStoreSource, /rename/);
  assert.doesNotMatch(secureStoreSource, /keyring|Keychain|Credential Manager/i);
  assert.doesNotMatch(
    `${credentialSource}\n${secureStoreSource}`,
    /tauri::command|invoke_handler|Serialize|Deserialize/,
  );
  assert.doesNotMatch(
    rustEntry,
    /async\s+fn\s+(?:get|load|read|save|set|replace|delete)_?device_credential/i,
  );
  assert.doesNotMatch(
    rustEntry,
    /struct\s+\w*(?:Command|Summary)\s*\{[^}]*(?:credential|token|private_key)\s*:/is,
  );
});

test("React has no command that can read or write the long-lived credential", async () => {
  const sourceText = (await readFrontendSources()).join("\n");

  assert.doesNotMatch(
    sourceText,
    /device_credential_(?:get|load|set|save|replace|delete)|secure_credential/i,
  );
});
