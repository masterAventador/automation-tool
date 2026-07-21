import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);
const repositoryRoot = new URL("../../", import.meta.url);

test("H8-05 uses one dedicated hidden App and a strict real-process orchestrator", async () => {
  const [packageJson, tauriConfig, wdioConfig, spec, runner] = await Promise.all([
    readFile(new URL("package.json", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/tauri.executor-crash-recovery-e2e.conf.json", frontendRoot), "utf8"),
    readFile(new URL("wdio.executor-crash-recovery.conf.ts", frontendRoot), "utf8"),
    readFile(new URL("e2e-tauri/executor-crash-recovery.spec.ts", frontendRoot), "utf8"),
    readFile(new URL("scripts/run_h8_05_acceptance.py", repositoryRoot), "utf8"),
  ]);
  const configuration = JSON.parse(tauriConfig);

  assert.match(packageJson, /build:tauri:executor-crash-recovery-test/u);
  assert.equal(configuration.identifier, "com.aventador.automationtool.h805acceptance");
  assert.equal(configuration.app.windows.length, 1);
  assert.equal(configuration.app.windows[0].visible, false);
  assert.match(wdioConfig, /e2e-tauri\/executor-crash-recovery\.spec\.ts/u);
  assert.match(spec, /inject_executor_crash_for_acceptance/u);
  assert.match(spec, /自动恢复次数/u);
  assert.match(spec, /结果待确认/u);
  assert.match(runner, /build_signed_executor/u);
  assert.match(runner, /seed_local_checkpoint/u);
  assert.match(runner, /task\.outcome_uncertain/u);
  assert.match(runner, /restart_count/u);
  assert.match(runner, /matching_executor_processes/u);
  assert.doesNotMatch(spec, /mock|page\.reload|localStorage/iu);
});
