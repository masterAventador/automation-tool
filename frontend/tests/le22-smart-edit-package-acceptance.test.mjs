import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

test("LE-22 package journey uses one speech material and the formal editing UI", async () => {
  const [spec, runner, wdio, configText, packageText, gitignore] = await Promise.all([
    readFile(new URL("e2e-tauri/smart-edit-package.spec.ts", frontendRoot), "utf8"),
    readFile(
      new URL("../scripts/run_le_22_macos_package_acceptance.py", frontendRoot),
      "utf8",
    ),
    readFile(new URL("wdio.le22-macos-package.conf.ts", frontendRoot), "utf8"),
    readFile(
      new URL("src-tauri/tauri.le22-macos-package-e2e.conf.json", frontendRoot),
      "utf8",
    ),
    readFile(new URL("package.json", frontendRoot), "utf8"),
    readFile(new URL("../.gitignore", frontendRoot), "utf8"),
  ]);
  const config = JSON.parse(configText);
  const scripts = JSON.parse(packageText).scripts;

  for (const required of [
    "check_local_startup_environment",
    "prepare_material_library_for_acceptance",
    "AUTOMATION_TOOL_LE22_MODEL_KEY",
    "导入本机素材",
    "有语音",
    "QUILTER",
    "APOSTLE",
    "生成草稿",
    "当前修订：第 1 版",
    "原声轨道",
    "提交剪辑任务",
    "刷新任务",
    "成片已入库",
  ]) {
    assert.ok(spec.includes(required), `LE-22 package spec is missing ${required}`);
  }
  assert.doesNotMatch(spec, /一键直出片/u);
  assert.doesNotMatch(spec, /sessionStorage|localStorage|mock|stub/iu);
  assert.match(spec, /assert\.doesNotMatch\([^;]+旁白轨道/su);

  assert.equal(
    scripts["test:le-22-macos-package"],
    "uv run --project ../backend --locked python ../scripts/run_le_22_macos_package_acceptance.py",
  );
  assert.equal(config.identifier, "com.aventador.automationtool.le22macpackage");
  assert.equal(config.app.windows.length, 1);
  assert.equal(config.app.windows[0].visible, false);
  assert.deepEqual(config.bundle.targets, ["app"]);
  assert.equal(config.bundle.macOS.signingIdentity, "-");
  assert.match(wdio, /LE22_MAC_APP_BINARY/u);
  assert.match(wdio, /smart-edit-package\.spec\.ts/u);
  assert.match(wdio, /driverProvider:\s*"embedded"/u);

  for (const required of [
    "install_runtime_resources_and_sign",
    "stage_browser_distribution",
    "require_macos_target",
    "prepare_voice_fixture",
    "fill_disk_image",
    "audit_material_video_worker_candidate",
    "validate_le22_database_evidence",
    "validate_le22_ffprobe",
    "compare_pcm_envelopes",
    "hdiutil",
    "ditto",
    "ffprobe",
    "-count_frames",
    "s16le",
    "AUTOMATION_TOOL_LE18_ACCEPTANCE_PICKER",
    "AUTOMATION_TOOL_LE22_MODEL_KEY",
  ]) {
    assert.ok(runner.includes(required), `LE-22 macOS runner is missing ${required}`);
  }
  assert.equal(
    runner.match(/require_packaged_browser\(/gu)?.length,
    2,
    "the assembled App and the App copied back from the DMG must both pass the browser gate",
  );
  assert.match(runner, /local-executor\/package\/automation-tool-executor/u);
  assert.match(runner, /bundle_binary\(installed_application\)/u);
  assert.match(
    runner,
    /speech_transcript from materials "\s+"order by material_id"/u,
    "the evidence query must order by a column that exists on materials",
  );
  assert.match(
    runner,
    /finally:\n\s+stop_control_plane\(server\)\n\s+server = None/u,
    "the Control Plane must stop before the isolated PostgreSQL context exits",
  );
  assert.doesNotMatch(runner, /__import__\(/u);
  assert.doesNotMatch(runner, /mock|stub|sessionStorage|localStorage/iu);
  assert.match(gitignore, /^frontend\/dist-le22-mac\/$/mu);
});
