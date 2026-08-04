import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

test("EB-05 drives the distribution acceptance for the native target", async () => {
  const acceptance = await readFile(
    new URL("scripts/run_eb_05_acceptance.py", repositoryRoot),
    "utf8",
  );

  // Intel Mac 于 2026-08-04 退出交付目标。
  for (const target of ["macos-arm64", "windows-x86_64"]) {
    assert.match(acceptance, new RegExp(`"${target}"`, "u"));
  }
  assert.doesNotMatch(acceptance, /"macos-x86_64"/u);
  // 归档改由机器级构建缓存持有，脚本不得再自带一份 checkout 内路径——
  // 那正是 FIX-embedded-browser-archive-lookup.md 记的那类缺陷的来源。
  assert.doesNotMatch(acceptance, /\.local\/eb-04-windows/u);
  assert.match(acceptance, /from embedded_browser_archives import/u);
  assert.match(acceptance, /default_archives\(\)/u);
  assert.match(acceptance, /target = contract\.targets\[target_id\]/u);
  assert.match(acceptance, /target_id=target_id/u);
  assert.match(
    acceptance,
    /tampered = staging \/ Path\(\*target\.executable\.split\("\/"\)\)/u,
  );
  assert.doesNotMatch(acceptance, /must run on macOS arm64/u);
});
