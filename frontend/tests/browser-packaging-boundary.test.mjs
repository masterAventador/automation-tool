import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("B5-07 packages Playwright for an explicit system browser without a WebView path surface", async () => {
  const [project, spec, runtime, native, acceptance, probe, orchestrator] = await Promise.all([
    readRepositoryFile("backend/pyproject.toml"),
    readRepositoryFile("backend/automation-tool-executor.spec"),
    readRepositoryFile("backend/src/automation_tool/executor/browser_runtime.py"),
    readRepositoryFile("frontend/src-tauri/src/lib.rs"),
    readRepositoryFile("frontend/src-tauri/tests/browser_packaged_runtime.rs"),
    readRepositoryFile("backend/tests/fixtures/packaged_browser_probe.py"),
    readRepositoryFile("backend/tests/integration/test_packaged_browser_probe.py"),
  ]);

  assert.match(project, /dependencies\s*=\s*\[[\s\S]*["']playwright/u);
  assert.match(spec, /collect_all\(["']playwright["']\)/u);
  assert.match(spec, /automation_tool\.executor\.browser_runtime/u);
  assert.match(runtime, /launch_persistent_context/u);
  assert.match(runtime, /executable_path/u);
  assert.match(runtime, /headless\s*=\s*False/u);
  assert.match(runtime, /accept_downloads\s*=\s*False/u);
  assert.doesNotMatch(runtime, /playwright\s+install|install\s+chromium|channel\s*=/u);
  assert.match(acceptance, /try_acquire_lock/u);
  assert.match(acceptance, /revalidate_(?:macos|windows)_browser/u);
  assert.match(probe, /sys\.stdout\.buffer\.write\(_READY\)/u);
  assert.match(probe, /_READY = b["']browser\.runtime\.ready\\n["']/u);
  assert.match(orchestrator, /request\.addfinalizer\(cleanup\)/u);
  assert.match(orchestrator, /shutil\.rmtree\(tmp_path\)/u);
  assert.match(orchestrator, /automation_tool\.executor\.package_manifest/u);
  assert.doesNotMatch(native, /tauri::command[\s\S]{0,300}browser_(?:executable|profile)_path/u);
});
