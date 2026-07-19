import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, frontendRoot), "utf8");
}

test("E4-05 keeps Executor package trust inside Rust with fixed cross-language contracts", async () => {
  const [cargo, rustEntry, verifier, manifest, signature, schema] = await Promise.all([
    readProjectFile("src-tauri/Cargo.toml"),
    readProjectFile("src-tauri/src/lib.rs"),
    readProjectFile("src-tauri/src/executor_package.rs"),
    readProjectFile(
      "../contracts/fixtures/executor-package-v1/valid/executor-manifest.v1.json",
    ),
    readProjectFile("../contracts/fixtures/executor-package-v1/valid/executor-manifest.v1.sig"),
    readProjectFile("../contracts/protocol/executor-package-manifest-v1.schema.json"),
  ]);

  assert.match(rustEntry, /pub mod executor_package;/);
  assert.match(cargo, /semver\s*=\s*"=1\.0\.28"/);
  assert.match(cargo, /sha2\s*=\s*\{[^}]*version\s*=\s*"=0\.11\.0"/);
  assert.match(cargo, /walkdir\s*=\s*"=2\.5\.0"/);
  assert.match(verifier, /VerifyingKey/);
  assert.match(verifier, /verify_strict/);
  assert.match(verifier, /VersionReq/);
  assert.match(verifier, /std::env::consts::OS/);
  assert.match(verifier, /std::env::consts::ARCH/);
  assert.match(verifier, /deny_unknown_fields/);
  assert.match(verifier, /executor-manifest\.v1\.json/);
  assert.match(verifier, /executor-manifest\.v1\.sig/);
  assert.doesNotMatch(verifier, /#\[tauri::command\]/);
  assert.doesNotMatch(verifier, /std::env::var/);
  assert.doesNotMatch(verifier, /reqwest|https?:\/\//);
  assert.match(manifest, /"manifest_version":"1"/);
  assert.match(signature, /^atems1\.[A-Za-z0-9_-]{86}\n$/);
  assert.match(schema, /"additionalProperties": false/);
});
