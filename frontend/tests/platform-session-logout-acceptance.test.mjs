import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("B5-14 safe logout uses one persistent gate and the hidden real App path", async () => {
  const [spec, native, profiles, repository, migration, worker, runner] = await Promise.all([
    readProjectFile("frontend/e2e-tauri/platform-session.spec.ts"),
    readProjectFile("frontend/src-tauri/src/lib.rs"),
    readProjectFile("frontend/src-tauri/src/browser_profiles.rs"),
    readProjectFile(
      "backend/src/automation_tool/control_plane/infrastructure/database/task_repository.py",
    ),
    readProjectFile("backend/migrations/versions/20260718_0015_platform_session_gates.py"),
    readProjectFile("backend/src/automation_tool/executor/platform_commands.py"),
    readProjectFile("scripts/run_b5_13_acceptance.py"),
  ]);

  assert.match(spec, /button=安全注销/u);
  assert.match(spec, /button=确认注销/u);
  assert.match(spec, /create_douyin_search_exposure_task/u);
  assert.match(native, /prepare_douyin_platform_session_logout/u);
  assert.match(native, /emergency_stop\(\)[\s\S]*remove_current_douyin_profile/u);
  assert.match(native, /CompleteDouyinLogout/u);
  assert.match(profiles, /remove_current_douyin_profile/u);
  assert.match(repository, /platform_session_gates/u);
  assert.match(migration, /state = 'blocked'/u);
  assert.match(worker, /douyin\.logout\.complete/u);
  assert.match(runner, /verify_logout_local_state/u);
  assert.match(runner, /require_no_residual_project_processes/u);
  assert.doesNotMatch(spec, /profileDirectory|executablePath/u);
});
