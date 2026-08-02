import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("B5-13 drives platform health and handling through one hidden real App", async () => {
  const [spec, config, packageJson, native, worker, cli, runner] = await Promise.all([
    readProjectFile("frontend/e2e-tauri/platform-session.spec.ts"),
    readProjectFile("frontend/src-tauri/tauri.platform-session-e2e.conf.json"),
    readProjectFile("frontend/package.json"),
    readProjectFile("frontend/src-tauri/src/lib.rs"),
    readProjectFile("backend/src/automation_tool/executor/platform_commands.py"),
    readProjectFile("backend/src/automation_tool/executor/cli.py"),
    readProjectFile("scripts/run_b5_13_acceptance.py"),
  ]);

  assert.match(config, /com\.aventador\.automationtool\.b513acceptance/u);
  assert.match(config, /"visible": false/u);
  assert.match(spec, /openWorkbenchSection\("账号与平台"\)/u);
  assert.match(spec, /toHaveText\("账号与平台"\)/u);
  assert.doesNotMatch(spec, /li=平台状态/u);
  assert.match(spec, /button=打开登录处理/u);
  assert.match(spec, /button=我已处理，重新检查/u);
  assert.match(spec, /exit_app_for_acceptance/u);
  assert.match(native, /fn open_douyin_login/u);
  assert.match(native, /fn recheck_douyin_login/u);
  assert.match(native, /cfg!\(feature\s*=\s*"control-plane-e2e"\)/u);
  assert.match(worker, /DouyinQrLoginFlow/u);
  assert.match(cli, /DouyinSessionHealthReporter/u);
  assert.match(packageJson, /test:platform-session-tauri/u);
  assert.match(runner, /platform_session_health/u);
  assert.match(runner, /require_no_residual_project_processes/u);
});
