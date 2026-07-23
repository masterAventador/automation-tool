import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("B5-08 owns one thread-confined browser context with bounded window operations", async () => {
  const [runtime, lifecycle, probe, native, acceptance, architecture] = await Promise.all([
    readRepositoryFile("backend/src/automation_tool/executor/browser_runtime.py"),
    readRepositoryFile("backend/tests/unit/executor/test_browser_runtime_lifecycle.py"),
    readRepositoryFile("backend/tests/fixtures/packaged_browser_probe.py"),
    readRepositoryFile("frontend/src-tauri/src/managed_process_tree.rs"),
    readRepositoryFile("frontend/src-tauri/tests/browser_packaged_runtime.rs"),
    readRepositoryFile("docs/backend-architecture.md"),
  ]);

  assert.match(runtime, /class BrowserRuntime:/u);
  assert.match(runtime, /class BrowserWindow:/u);
  assert.match(runtime, /capture_window/u);
  assert.match(runtime, /set_default_timeout/u);
  assert.match(runtime, /set_default_navigation_timeout/u);
  assert.match(runtime, /get_ident/u);
  assert.match(runtime, /playwright\.stop/u);
  assert.match(lifecycle, /use_after_close|use after close|rejects_use_after_close/u);
  assert.match(probe, /primary_window/u);
  assert.match(probe, /open_window/u);
  assert.match(native, /process_group\(0\)/u);
  assert.match(native, /JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE/u);
  assert.match(acceptance, /SIGKILL/u);
  assert.match(acceptance, /ExecutorManager/u);
  assert.match(
    acceptance,
    /#\[cfg\(target_os = "windows"\)\][\s\S]*packaged_runtime_hard_stop_terminates_the_complete_browser_process_tree/u,
  );
  assert.match(acceptance, /profile\.revalidate/u);
  assert.match(architecture, /Local Executor[\s\S]*Playwright/u);
  assert.doesNotMatch(runtime, /tauri|Control Plane|cookie/u);
});
