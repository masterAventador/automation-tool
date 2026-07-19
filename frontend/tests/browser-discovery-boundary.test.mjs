import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("B5-02 keeps macOS browser discovery in a fixed native trust boundary", async () => {
  const [source, library, cargo, integration] = await Promise.all([
    readRepositoryFile("frontend/src-tauri/src/browser_discovery.rs"),
    readRepositoryFile("frontend/src-tauri/src/lib.rs"),
    readRepositoryFile("frontend/src-tauri/Cargo.toml"),
    readRepositoryFile("frontend/src-tauri/tests/browser_discovery.rs"),
  ]);

  assert.match(library, /pub mod browser_discovery;/u);
  assert.match(cargo, /target\.'cfg\(target_os = "macos"\)'\.dependencies/u);
  assert.match(cargo, /security-framework\s*=\s*\{ version = "=3\.7\.0"/u);
  assert.match(cargo, /core-foundation\s*=\s*"=0\.10\.1"/u);

  for (const fixedBoundary of [
    "/Applications/Google Chrome.app",
    "/Applications/Microsoft Edge.app",
    "Contents/MacOS/Google Chrome",
    "Contents/MacOS/Microsoft Edge",
    "com.google.Chrome",
    "com.microsoft.edgemac",
    "EQHXZ8M8AV",
    "UBF8T346G9",
    "CHECK_ALL_ARCHITECTURES",
    "CHECK_NESTED_CODE",
    "RESTRICT_SYMLINKS",
  ]) {
    assert.match(source, new RegExp(fixedBoundary.replaceAll("/", "\\/"), "u"));
  }

  for (const requiredFailure of [
    "CandidateRejected",
    "PathInvalidated",
    "UnsupportedPlatform",
    "revalidate_macos_browser",
  ]) {
    assert.match(source, new RegExp(requiredFailure, "u"));
  }

  assert.doesNotMatch(source, /std::process::Command|\/usr\/bin\/codesign|HOME|home_dir/u);
  assert.doesNotMatch(library, /tauri::command[\s\S]{0,300}browser.*(?:path|executable)/iu);
  assert.match(integration, /real_installed_macos_browsers_use_the_production_signature_path/u);
  assert.match(integration, /revalidate_macos_browser/u);
});
