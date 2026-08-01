import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);
const frontendRoot = new URL("frontend/", repositoryRoot);

async function source(relativePath) {
  return readFile(new URL(relativePath, repositoryRoot), "utf8");
}

test("LE-17 owns one real App editing journey with an isolated production runtime", async () => {
  const [spec, wdio, tauri, runner, packageText] = await Promise.all([
    readFile(new URL("e2e-tauri/video-editing.spec.ts", frontendRoot), "utf8"),
    readFile(new URL("wdio.video-editing.conf.ts", frontendRoot), "utf8"),
    readFile(
      new URL("src-tauri/tauri.video-editing-e2e.conf.json", frontendRoot),
      "utf8",
    ),
    source("scripts/run_le_17_acceptance.py"),
    readFile(new URL("package.json", frontendRoot), "utf8"),
  ]);
  const configuration = JSON.parse(tauri);
  const scripts = JSON.parse(packageText).scripts;

  assert.match(spec, /button=提交剪辑任务/u);
  assert.match(spec, /成片已入库/u);
  assert.match(spec, /已完成/u);
  assert.match(spec, /时间轴还不完整/u);
  assert.match(spec, /本机剪辑服务暂时不可用/u);
  assert.match(spec, /提交结果暂时无法确认/u);
  assert.match(spec, /includes\("尚未保存"\)/u);
  assert.match(spec, /getValue\(\)/u);
  assert.doesNotMatch(spec, /云端剪辑功能尚未开通|sessionStorage|localStorage/u);
  assert.doesNotMatch(spec, /mock|stub|setItem\(/iu);

  assert.match(wdio, /specs:\s*\["\.\/e2e-tauri\/video-editing\.spec\.ts"\]/u);
  assert.doesNotMatch(wdio, /video-studio\.spec\.ts|motion-video-native\.spec\.ts/u);
  assert.equal(configuration.identifier, "com.aventador.automationtool.le17acceptance");
  assert.equal(configuration.app.windows.length, 1);
  assert.equal(configuration.app.windows[0].visible, false);
  assert.match(
    scripts["build:tauri:video-editing-test"],
    /--features control-plane-e2e/u,
  );
  assert.match(
    scripts["build:tauri:video-editing-test"],
    /tauri\.video-editing-e2e\.conf\.json/u,
  );
  assert.equal(
    scripts["test:le17-video-editing-app"],
    "../backend/.venv/bin/python ../scripts/run_le_17_acceptance.py",
  );

  for (const required of [
    "video_studio_startup_harness",
    "prepare_video_runtime",
    "install_video_runtime",
    "MaterialPathRegistry",
    "wdio.video-editing.conf.ts",
    "ffprobe",
    "video-workspaces-v1",
    "editing_job_diagnostics",
  ]) {
    assert.ok(runner.includes(required), `acceptance runner is missing ${required}`);
  }
  assert.match(runner, /state\.mkdir\(mode=0o700, parents=True\)/u);
  assert.match(runner, /if os\.name != "nt":\s+state\.chmod\(0o700\)/u);
  assert.match(
    runner,
    /video_studio_startup_harness\([\s\S]*demo_environment_id=ENVIRONMENT_ID,[\s\S]*demo_bootstrap_public_key=public_key,/u,
  );
  assert.doesNotMatch(runner, /mock|stub|sessionStorage|localStorage/iu);
});
