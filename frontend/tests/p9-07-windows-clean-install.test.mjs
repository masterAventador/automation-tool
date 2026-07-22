import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);
const repositoryRoot = new URL("../../", import.meta.url);

test("P9-07 exposes one explicit Windows device command without entering CI", async () => {
  const [packageSource, runner, workflow] = await Promise.all([
    readFile(new URL("package.json", frontendRoot), "utf8"),
    readFile(new URL("scripts/run_p9_07_acceptance.py", repositoryRoot), "utf8"),
    readFile(new URL(".github/workflows/desktop.yml", repositoryRoot), "utf8"),
  ]);
  const packageDocument = JSON.parse(packageSource);

  assert.equal(
    packageDocument.scripts["test:p9-07-windows-clean-install"],
    "uv run --project ../backend --locked python ../scripts/run_p9_07_acceptance.py --interactive-device-acceptance",
  );
  assert.match(runner, /sys\.platform\s*!=\s*["']win32["']/u);
  assert.match(runner, /--interactive-device-acceptance/u);
  assert.match(runner, /isatty\(\)/u);
  assert.match(runner, /require_non_elevated_process/u);
  assert.match(workflow, /run_p9_07_acceptance\.py/u);
  assert.doesNotMatch(workflow, /test:p9-07-windows-clean-install/u);
});

test("P9-07 accepts only one timestamped Authenticode installer and HKCU install", async () => {
  const runner = await readFile(
    new URL("scripts/run_p9_07_acceptance.py", repositoryRoot),
    "utf8",
  );

  assert.match(runner, /Get-AuthenticodeSignature/u);
  assert.match(runner, /TimeStamperCertificate/u);
  assert.match(runner, /1\.3\.6\.1\.5\.5\.7\.3\.3/u);
  assert.match(runner, /X509Chain/u);
  assert.match(runner, /HKEY_CURRENT_USER/u);
  assert.match(runner, /HKEY_LOCAL_MACHINE/u);
  assert.match(runner, /DisplayName/u);
  assert.match(runner, /InstallLocation/u);
  assert.match(runner, /UninstallString/u);
  assert.match(runner, /["']\/S["']/u);
  assert.match(runner, /uninstall\.exe/u);
  assert.match(runner, /audit-release-bundle\.mjs/u);
  assert.doesNotMatch(runner, /tauri["'],\s*["']build|build_windows_executor_candidate/u);
});

test("P9-07 verifies high DPI and Job-owned Python-free runtime before a main-process kill", async () => {
  const runner = await readFile(
    new URL("scripts/run_p9_07_acceptance.py", repositoryRoot),
    "utf8",
  );

  assert.match(runner, /GetProcessDpiAwareness/u);
  assert.match(runner, /GetDpiForWindow/u);
  assert.match(runner, /window_dpi\s*<\s*120/u);
  assert.match(runner, /IsProcessInJob/u);
  assert.match(runner, /Stop-Process/u);
  assert.doesNotMatch(runner, /taskkill|\/T/u);
  assert.match(runner, /python(?:3(?:\.\d+)?)?/iu);
  assert.match(runner, /chrome\.exe|msedge\.exe/u);
  assert.match(runner, /--user-data-dir/u);
  assert.match(runner, /browser-profiles/u);
  assert.match(runner, /local-executor/u);
});

test("P9-07 records scan and crash recovery, then proves uninstall preserves private data", async () => {
  const runner = await readFile(
    new URL("scripts/run_p9_07_acceptance.py", repositoryRoot),
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
    "forced_exit_recovered",
  ]) {
    assert.match(runner, new RegExp(checkpoint, "u"));
  }
  assert.match(runner, /p9-07\.windows-clean-install\.v1/u);
  assert.match(runner, /packageSha256/u);
  assert.match(runner, /windowDpi/u);
  assert.match(runner, /pythonDescendantCount/u);
  assert.match(runner, /SetAccessRuleProtection/u);
  assert.match(runner, /SendToRecycleBin/u);
  assert.match(runner, /private AppData was removed by uninstall/u);
  assert.doesNotMatch(runner, /wdio|webdriver|desktop-e2e|control-plane-e2e|headless/iu);
});
