import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);
const frontendRoot = new URL("frontend/", repositoryRoot);

async function source(relativePath) {
  return readFile(new URL(relativePath, repositoryRoot), "utf8");
}

test("LE-18 owns one isolated real App material-library journey", async () => {
  const [spec, wdio, tauriText, runner, packageText, native] = await Promise.all([
    readFile(new URL("e2e-tauri/material-library.spec.ts", frontendRoot), "utf8"),
    readFile(new URL("wdio.material-library.conf.ts", frontendRoot), "utf8"),
    readFile(
      new URL("src-tauri/tauri.material-library-e2e.conf.json", frontendRoot),
      "utf8",
    ),
    source("scripts/run_le_18_acceptance.py"),
    readFile(new URL("package.json", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/src/lib.rs", frontendRoot), "utf8"),
  ]);
  const configuration = JSON.parse(tauriText);
  const scripts = JSON.parse(packageText).scripts;

  for (const required of [
    "视频",
    "音频",
    "图片",
    "这个文件已经在素材库里",
    "人工说明已保存",
    "本机文件不在原位置了",
    "本机文件仍在原位置，但当前无法读取",
    "本机文件已经被替换或修改",
    "暂时不能删除这个素材",
    "素材已从素材库删除",
    "打开本机预览",
  ]) {
    assert.ok(spec.includes(required), `acceptance spec is missing ${required}`);
  }
  assert.match(spec, /core\.invoke\("prepare_material_library_for_acceptance"\)/u);
  assert.match(spec, /new RegExp\(UUID_V4_SOURCE, "g"\)/u);
  assert.doesNotMatch(spec, /const UUID_V4\s*=\s*\/[^^\n]*\^/u);
  assert.match(spec, /core\.invoke\("set_material_source_state_for_acceptance"/u);
  assert.match(spec, /setSourceState\(missingMaterialId, "missing"\)/u);
  assert.match(spec, /setSourceState\(unreadableMaterialId, "unreadable"\)/u);
  assert.match(spec, /setSourceState\(changedMaterialId, "changed"\)/u);
  assert.doesNotMatch(spec, /sessionStorage|localStorage|mock|stub/iu);

  assert.match(wdio, /specs:\s*\["\.\/e2e-tauri\/material-library\.spec\.ts"\]/u);
  assert.match(wdio, /driverProvider:\s*"embedded"/u);
  assert.equal(configuration.identifier, "com.aventador.automationtool.le18acceptance");
  assert.equal(configuration.app.windows.length, 1);
  assert.equal(configuration.app.windows[0].visible, false);
  assert.match(
    scripts["build:tauri:material-library-test"],
    /--features control-plane-e2e/u,
  );
  assert.equal(
    scripts["test:le18-material-library-app"],
    "../backend/.venv/bin/python ../scripts/run_le_18_acceptance.py",
  );

  for (const required of [
    "video_studio_startup_harness",
    "prepare_video_runtime",
    "install_video_runtime",
    "AUTOMATION_TOOL_LE18_PICK_{index}",
    "assert_database_outcome",
    "assert_no_private_evidence",
    "source_unchanged_after_delete",
  ]) {
    assert.ok(runner.includes(required), `acceptance runner is missing ${required}`);
  }
  assert.match(runner, /state\.mkdir\(mode=0o700, parents=True\)/u);
  assert.match(runner, /if os\.name != "nt":\s+state\.chmod\(0o700\)/u);
  assert.doesNotMatch(runner, /mock|stub|sessionStorage|localStorage/iu);

  assert.match(native, /app\.dialog\(\)\.file\(\)\.blocking_pick_file\(\)/u);
  assert.match(native, /AUTOMATION_TOOL_LE18_ACCEPTANCE_PICKER/u);
  assert.match(native, /AUTOMATION_TOOL_LE18_PICK_\{index\}/u);
  assert.match(native, /cfg\(feature = "control-plane-e2e"\)/u);
});
