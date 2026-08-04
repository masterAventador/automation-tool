import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

// Intel Mac 于 2026-08-04 退出交付目标，所以这条测试原来的名字（「区分两种 macOS
// 架构」）已经没有第二种架构可区分了。但**它要防的事没有变**：包里混进别的架构必须
// 被拒。所以 `0x01000007`（x86_64）这条断言留着——它现在是这个性质唯一的守卫，
// 与 `verify_macos_chromium_archive.py` 里那个 `FOREIGN_CPU_TYPE_X86_64` 常量成对。
test("embedded Chromium ships one macOS target and one Windows target, and refuses a foreign macOS architecture", async () => {
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

  assert.deepEqual(targetIds, ["macos-arm64", "windows-x86_64"]);
  assert.deepEqual(Object.keys(staging.targets).sort(), targetIds);
  // 说清楚这里少的是什么，别让后来者以为漏写了一个目标。
  assert.ok(
    !targetIds.includes("macos-x86_64"),
    "Intel Mac 已退出交付目标；要重新支持它得先改这条断言，而不是悄悄加回契约",
  );

  const arm = staging.targets["macos-arm64"];
  assert.equal(arm.root_entry, "chrome-mac-arm64");
  assert.match(arm.download_url, /\/mac-arm64\/chrome-mac-arm64\.zip$/u);

  for (const source of [distribution, authority]) {
    assert.match(
      source,
      /target_os = "macos", target_arch = "aarch64"[\s\S]{0,100}"macos-arm64"/u,
    );
    assert.doesNotMatch(
      source,
      /"macos-x86_64"/u,
      "Intel Mac 的目标标识不该再出现在发行物解析里",
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
