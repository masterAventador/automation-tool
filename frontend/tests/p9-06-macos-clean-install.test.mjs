import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);
const repositoryRoot = new URL("../../", import.meta.url);

test("P9-06 exposes one explicit macOS device acceptance command without entering CI", async () => {
  const [packageSource, runnerSource, workflowSource] = await Promise.all([
    readFile(new URL("package.json", frontendRoot), "utf8"),
    readFile(new URL("scripts/run_p9_06_acceptance.py", repositoryRoot), "utf8"),
    readFile(new URL(".github/workflows/desktop.yml", repositoryRoot), "utf8"),
  ]);
  const packageDocument = JSON.parse(packageSource);

  assert.equal(
    packageDocument.scripts["test:p9-06-macos-clean-install"],
    "../backend/.venv/bin/python ../scripts/run_p9_06_acceptance.py --interactive-device-acceptance",
  );
  assert.match(runnerSource, /sys\.platform\s*!=\s*["']darwin["']/u);
  assert.match(runnerSource, /--interactive-device-acceptance/u);
  assert.match(runnerSource, /isatty\(\)/u);
  assert.match(runnerSource, /geteuid\(\)\s*==\s*0/u);
  assert.match(workflowSource, /run_p9_06_acceptance\.py/u);
  assert.doesNotMatch(workflowSource, /test:p9-06-macos-clean-install/u);
});

test("P9-06 accepts only a signed notarized DMG and performs a clean per-user install", async () => {
  const runner = await readFile(
    new URL("scripts/run_p9_06_acceptance.py", repositoryRoot),
    "utf8",
  );

  assert.match(runner, /hdiutil["'],\s*["']verify/u);
  assert.match(runner, /hdiutil["'],\s*["']attach/u);
  assert.match(runner, /["']-readonly["']/u);
  assert.match(runner, /codesign["'],\s*["']--verify["'],\s*["']--deep["'],\s*["']--strict["']/u);
  assert.match(runner, /spctl["'],\s*["']--assess["']/u);
  assert.match(runner, /stapler["'],\s*["']validate["']/u);
  assert.match(runner, /com\.aventador\.automationtool/u);
  assert.match(runner, /Library["']\s*\/\s*["']Application Support/u);
  assert.match(runner, /Applications["']\s*\/\s*INSTALL_NAME/u);
  assert.match(runner, /Refusing to reuse/u);
  assert.match(runner, /audit-release-bundle\.mjs/u);
  assert.doesNotMatch(runner, /tauri["'],\s*["']build|build_macos_executor_candidate/u);
});

test("P9-06 proves the installed App has no Python runtime dependency and owns its browser profile", async () => {
  const runner = await readFile(
    new URL("scripts/run_p9_06_acceptance.py", repositoryRoot),
    "utf8",
  );

  for (const variable of ["PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "AUTOMATION_TOOL_"]) {
    assert.match(runner, new RegExp(variable, "u"));
  }
  assert.match(runner, /\/usr\/bin:\/bin:\/usr\/sbin:\/sbin/u);
  assert.match(runner, /python(?:3(?:\.\d+)?)?/iu);
  assert.match(runner, /Google Chrome|Microsoft Edge/u);
  assert.match(runner, /--user-data-dir/u);
  assert.match(runner, /browser-profiles/u);
  assert.match(runner, /local-executor/u);
  assert.doesNotMatch(runner, /Default\/Cookies|Login Data|storage_state/iu);
});

test("P9-06 records operator-observed scan, task, and restart recovery without a test driver", async () => {
  const runner = await readFile(
    new URL("scripts/run_p9_06_acceptance.py", repositoryRoot),
    "utf8",
  );

  for (const checkpoint of [
    "startup_diagnostics_ready",
    "platform_scan_detected",
    "task_preview_confirmed",
    "controlled_task_completed",
    "structured_results_visible",
    "platform_session_reused",
    "task_snapshot_recovered",
    "no_duplicate_action",
  ]) {
    assert.match(runner, new RegExp(checkpoint, "u"));
  }
  assert.match(runner, /schemaVersion/u);
  assert.match(runner, /p9-06\.macos-clean-install\.v1/u);
  assert.match(runner, /osVersion/u);
  assert.match(runner, /packageSha256/u);
  assert.match(runner, /browser/u);
  assert.match(runner, /pythonDescendantCount/u);
  assert.match(runner, /mode=0o600/u);
  assert.doesNotMatch(runner, /wdio|webdriver|desktop-e2e|control-plane-e2e|headless/iu);
});
