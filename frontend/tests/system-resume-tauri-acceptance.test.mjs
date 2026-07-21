import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);
const repositoryRoot = new URL("../../", import.meta.url);

test("H8-08 owns one hidden App system-resume recovery boundary", async () => {
  const [packageJson, tauriConfig, wdioConfig, spec, runner, runtime, browserRuntime, rustEntry] =
    await Promise.all([
      readFile(new URL("package.json", frontendRoot), "utf8"),
      readFile(new URL("src-tauri/tauri.system-resume-e2e.conf.json", frontendRoot), "utf8"),
      readFile(new URL("wdio.system-resume.conf.ts", frontendRoot), "utf8"),
      readFile(new URL("e2e-tauri/system-resume.spec.ts", frontendRoot), "utf8"),
      readFile(new URL("scripts/run_h8_08_acceptance.py", repositoryRoot), "utf8"),
      readFile(
        new URL("backend/src/automation_tool/executor/runtime.py", repositoryRoot),
        "utf8",
      ),
      readFile(
        new URL("backend/src/automation_tool/executor/browser_runtime.py", repositoryRoot),
        "utf8",
      ),
      readFile(new URL("src-tauri/src/lib.rs", frontendRoot), "utf8"),
    ]);
  const configuration = JSON.parse(tauriConfig);

  assert.match(packageJson, /build:tauri:system-resume-test/u);
  assert.match(packageJson, /test:h8-08-tauri/u);
  assert.equal(configuration.identifier, "com.aventador.automationtool.h808acceptance");
  assert.equal(configuration.app.windows.length, 1);
  assert.equal(configuration.app.windows[0].visible, false);
  assert.match(wdioConfig, /e2e-tauri\/system-resume\.spec\.ts/u);
  assert.match(spec, /prepare_system_resume_for_acceptance/u);
  assert.match(spec, /restart_executor/u);
  assert.match(spec, /get_executor_diagnostics/u);
  assert.match(spec, /system_suspension_detected/u);
  assert.match(spec, /transport_recovered/u);
  assert.match(spec, /restartCount/u);
  assert.match(spec, /AUTOMATION_TOOL_H808_FACTS_VERIFIED_SIGNAL/u);
  assert.match(rustEntry, /prepare_system_resume_for_acceptance/u);
  assert.match(runner, /build_signed_executor/u);
  assert.match(runner, /suspend_executor/u);
  assert.match(runner, /resume_executor/u);
  assert.match(runner, /verify_headless_window_recovery/u);
  assert.match(runner, /matching_executor_processes/u);
  assert.match(runtime, /_suspension_detected/u);
  assert.match(runtime, /ExecutorCommandExpired/u);
  assert.match(browserRuntime, /browser_window_unavailable/u);
  assert.doesNotMatch(spec, /mock|localStorage/iu);
});
