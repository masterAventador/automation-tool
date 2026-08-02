import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);
const frontendRoot = new URL("frontend/", repositoryRoot);

async function source(relativePath) {
  return readFile(new URL(relativePath, repositoryRoot), "utf8");
}

test("LE-19 owns one isolated real App smart-edit normal and failure journey", async () => {
  const [spec, wdio, tauriText, runner, packageText, measurementProtocol] = await Promise.all([
    readFile(new URL("e2e-tauri/smart-edit.spec.ts", frontendRoot), "utf8"),
    readFile(new URL("wdio.smart-edit.conf.ts", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/tauri.smart-edit-e2e.conf.json", frontendRoot), "utf8"),
    source("scripts/run_le_19_acceptance.py"),
    readFile(new URL("package.json", frontendRoot), "utf8"),
    source("scripts/le24_measurement.py"),
  ]);
  const configuration = JSON.parse(tauriText);
  const scripts = JSON.parse(packageText).scripts;

  for (const required of [
    "prepare_material_library_for_acceptance",
    "导入本机素材",
    "智能剪辑尚未配置完成",
    "文案模型服务 API Key",
    "生成草稿",
    "草稿已生成并放入时间轴",
    "一键直出片",
    "草稿已生成，成片任务正在排队",
    "成片已入库",
    "正在理解素材",
  ]) {
    assert.ok(spec.includes(required), `acceptance spec is missing ${required}`);
  }
  assert.match(spec, /waitForSmartResult\(configured, 1, true\)/u);
  assert.match(spec, /waitForSmartResult\(configured, 2, false\)/u);
  assert.match(spec, /text\.includes\(`时间轴第 \$\{expectedRevision\} 版`\)/u);
  assert.doesNotMatch(spec, /sessionStorage|localStorage|mock|stub/iu);
  assert.match(spec, /AUTOMATION_TOOL_LE19_MODEL_KEY/u);
  assert.match(spec, /AUTOMATION_TOOL_LE24_MEASURE_THINKING/u);
  assert.match(spec, /AUTOMATION_TOOL_LE24_MATERIAL_COUNT/u);
  assert.match(spec, /performance\.now\(\)/u);
  assert.match(spec, /LE24_MEASUREMENT/u);
  assert.match(spec, /async function materialIds/u);
  assert.match(spec, /\(await materialIds\(editing\)\)\.length > before\.size/u);
  assert.match(
    spec,
    /async function openCurrentEditingProject[\s\S]*button=打开时间轴编辑/u,
  );
  assert.equal(
    spec.match(/await openCurrentEditingProject\(configured\)/gu)?.length,
    2,
    "normal and measurement journeys must both reopen the project after settings",
  );

  assert.match(wdio, /specs:\s*\["\.\/e2e-tauri\/smart-edit\.spec\.ts"\]/u);
  assert.match(wdio, /driverProvider:\s*"embedded"/u);
  assert.equal(configuration.identifier, "com.aventador.automationtool.le19acceptance");
  assert.equal(configuration.app.windows.length, 1);
  assert.equal(configuration.app.windows[0].visible, false);
  assert.match(scripts["build:tauri:smart-edit-test"], /--features control-plane-e2e/u);
  assert.equal(
    scripts["test:le19-smart-edit-app"],
    "../backend/.venv/bin/python ../scripts/run_le_19_acceptance.py",
  );

  for (const required of [
    "video_studio_startup_harness",
    "prepare_video_runtime",
    "install_video_runtime",
    "bailian-model.json",
    "AUTOMATION_TOOL_LE19_MODEL_KEY",
    "assert_database_outcome",
    "ffprobe",
    "editing_job_diagnostics",
    "assert_no_private_evidence",
    "read_le24_measurement_request",
    "parse_le24_measurement",
    "MEASUREMENT_MARKER",
  ]) {
    assert.ok(runner.includes(required), `acceptance runner is missing ${required}`);
  }
  for (const required of [
    "AUTOMATION_TOOL_LE24_MEASURE_THINKING",
    "AUTOMATION_TOOL_LE24_MATERIAL_COUNT",
    "LE24_MEASUREMENT",
  ]) {
    assert.ok(
      measurementProtocol.includes(required),
      `measurement protocol is missing ${required}`,
    );
  }
  assert.doesNotMatch(runner, /\[\s*str\(ffprobe\),\s*str\(ffprobe\),/u);
  assert.doesNotMatch(runner, /from materials order by created_at/u);
  assert.match(runner, /from materials order by material_id/u);
  assert.doesNotMatch(runner, /mock|stub|sessionStorage|localStorage/iu);
});
