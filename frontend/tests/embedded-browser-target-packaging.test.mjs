import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("embedded Chromium distinguishes both macOS architectures and one Windows target", async () => {
  const [compatibilityText, stagingText, distribution, authority, archiveVerifier] = await Promise.all([
    readRepositoryFile("contracts/browser/embedded-chromium-compatibility.v1.json"),
    readRepositoryFile("contracts/browser/embedded-chromium-staging.v1.json"),
    readRepositoryFile("frontend/src-tauri/src/embedded_browser_distribution.rs"),
    readRepositoryFile("frontend/src-tauri/src/embedded_browser_authority.rs"),
    readRepositoryFile("scripts/verify_macos_chromium_archive.py"),
  ]);
  const compatibility = JSON.parse(compatibilityText);
  const staging = JSON.parse(stagingText);
  const targetIds = compatibility.supported_targets.map(({ id }) => id).sort();

  assert.deepEqual(targetIds, [
    "macos-arm64",
    "macos-x86_64",
    "windows-x86_64",
  ]);
  assert.deepEqual(Object.keys(staging.targets).sort(), targetIds);

  const arm = staging.targets["macos-arm64"];
  const intel = staging.targets["macos-x86_64"];
  assert.equal(arm.root_entry, "chrome-mac-arm64");
  assert.equal(intel.root_entry, "chrome-mac-x64");
  assert.notEqual(arm.archive_sha256, intel.archive_sha256);
  assert.match(arm.download_url, /\/mac-arm64\/chrome-mac-arm64\.zip$/u);
  assert.match(intel.download_url, /\/mac-x64\/chrome-mac-x64\.zip$/u);

  for (const source of [distribution, authority]) {
    assert.match(
      source,
      /target_os = "macos", target_arch = "aarch64"[\s\S]{0,100}"macos-arm64"/u,
    );
    assert.match(
      source,
      /target_os = "macos", target_arch = "x86_64"[\s\S]{0,100}"macos-x86_64"/u,
    );
  }
  assert.match(archiveVerifier, /0x0100000C/u);
  assert.match(archiveVerifier, /0x01000007/u);
  assert.match(archiveVerifier, /browser Mach-O architecture mismatch/u);
  assert.match(distribution, /manifest executable does not match release target/u);
  assert.match(distribution, /browser Mach-O architecture mismatch/u);
  assert.match(distribution, /browser PE architecture mismatch/u);
});

test("distribution verification rejects a second platform Chromium root", async () => {
  const [stagingBuilder, distributionBuilder, nativeDistribution] = await Promise.all([
    readRepositoryFile("scripts/build_embedded_chromium_staging.py"),
    readRepositoryFile("scripts/build_embedded_browser_distribution.py"),
    readRepositoryFile("frontend/src-tauri/src/embedded_browser_distribution.rs"),
  ]);

  assert.match(stagingBuilder, /if output\.exists\(\):[\s\S]{0,100}output directory already exists/u);
  assert.match(stagingBuilder, /roots != \[target\.root_entry\]/u);
  assert.match(distributionBuilder, /allowed_top_level/u);
  assert.match(distributionBuilder, /unexpected top-level distribution entry/u);
  assert.match(nativeDistribution, /unexpected top-level distribution entry/u);
});
