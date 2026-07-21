import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, frontendRoot), "utf8");
}

test("H8-04 retains a hidden two-process App crash recovery acceptance", async () => {
  const [packageJson, tauriConfig, wdioConfig, spec, rustEntry, orchestrator] =
    await Promise.all([
      readProjectFile("package.json"),
      readProjectFile("src-tauri/tauri.app-crash-recovery-e2e.conf.json"),
      readProjectFile("wdio.app-crash-recovery.conf.ts"),
      readProjectFile("e2e-tauri/app-crash-recovery.spec.ts"),
      readProjectFile("src-tauri/src/lib.rs"),
      readFile(
        new URL("../../scripts/run_h8_04_acceptance.py", import.meta.url),
        "utf8",
      ),
    ]);

  assert.match(packageJson, /test:h8-04-tauri/u);
  assert.match(packageJson, /build:tauri:app-crash-recovery-test/u);
  assert.match(tauriConfig, /com\.aventador\.automationtool\.h804acceptance/u);
  assert.match(tauriConfig, /"visible"\s*:\s*false/u);
  assert.match(wdioConfig, /e2e-tauri\/app-crash-recovery\.spec\.ts/u);
  assert.match(spec, /AUTOMATION_TOOL_H804_PHASE/u);
  assert.match(spec, /prepare_app_crash_recovery_for_acceptance/u);
  assert.match(spec, /app_process_id_for_acceptance/u);
  for (const fact of ["Executor 在线", "运行中", "任务开始", "步骤开始"]) {
    assert.match(spec, new RegExp(fact, "u"));
  }
  assert.match(rustEntry, /prepare_app_crash_recovery_for_acceptance/u);
  assert.match(rustEntry, /app_process_id_for_acceptance/u);
  assert.match(orchestrator, /build_signed_executor/u);
  assert.match(orchestrator, /signal\.SIGKILL/u);
  assert.match(orchestrator, /verify_database_state/u);
  assert.match(orchestrator, /verify_local_state/u);
});
