import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("B5-15 restarts one hidden real App/Profile and fails closed into handoff", async () => {
  const [spec, config, packageJson, runner, fixtureExecutor] = await Promise.all([
    readProjectFile("frontend/e2e-tauri/platform-session-reuse.spec.ts"),
    readProjectFile("frontend/src-tauri/tauri.platform-session-reuse-e2e.conf.json"),
    readProjectFile("frontend/package.json"),
    readProjectFile("scripts/run_b5_15_acceptance.py"),
    readProjectFile("backend/tests/fixtures/b5_15_executor.py"),
  ]);

  assert.match(config, /com\.aventador\.automationtool\.b515acceptance/u);
  assert.match(config, /"visible"\s*:\s*false/u);
  assert.match(packageJson, /test:platform-session-reuse-tauri/u);
  assert.match(spec, /AUTOMATION_TOOL_B515_PHASE/u);
  assert.match(spec, /first|restart|expired|risk/u);
  assert.match(spec, /exit_app_for_acceptance/u);
  assert.match(runner, /profile_identity/u);
  assert.match(runner, /require_no_residual_project_processes/u);
  assert.match(runner, /healthy[\s\S]*healthy[\s\S]*expired[\s\S]*risk/u);
  assert.match(fixtureExecutor, /AcceptanceBrowserRuntime/u);
  assert.match(fixtureExecutor, /https:\/\/www\.douyin\.com\/user\/self/u);
  assert.doesNotMatch(spec, /profileDirectory|executablePath|cookie/u);
});
