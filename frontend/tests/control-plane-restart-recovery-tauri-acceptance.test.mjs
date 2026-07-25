import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);
const repositoryRoot = new URL("../../", import.meta.url);

test("H8-06 restarts the real Control Plane around one hidden App and signed Executor", async () => {
  const [packageJson, tauriConfig, wdioConfig, spec, runner, rustEntry] = await Promise.all([
    readFile(new URL("package.json", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/tauri.control-plane-recovery-e2e.conf.json", frontendRoot), "utf8"),
    readFile(new URL("wdio.control-plane-recovery.conf.ts", frontendRoot), "utf8"),
    readFile(new URL("e2e-tauri/control-plane-recovery.spec.ts", frontendRoot), "utf8"),
    readFile(new URL("scripts/run_h8_06_acceptance.py", repositoryRoot), "utf8"),
    readFile(new URL("src-tauri/src/lib.rs", frontendRoot), "utf8"),
  ]);
  const configuration = JSON.parse(tauriConfig);

  assert.match(packageJson, /build:tauri:control-plane-recovery-test/u);
  assert.match(packageJson, /test:h8-06-tauri/u);
  assert.equal(configuration.identifier, "com.aventador.automationtool.h806acceptance");
  assert.equal(configuration.app.windows.length, 1);
  assert.equal(configuration.app.windows[0].visible, false);
  assert.match(wdioConfig, /e2e-tauri\/control-plane-recovery\.spec\.ts/u);
  assert.match(spec, /prepare_control_plane_recovery_for_acceptance/u);
  assert.match(spec, /restart_executor/u);
  assert.match(spec, /取消命令已提交/u);
  assert.match(spec, /控制服务不可用/u);
  assert.match(spec, /restartCount/u);
  assert.match(rustEntry, /prepare_control_plane_recovery_for_acceptance/u);
  assert.match(runner, /build_signed_executor/u);
  assert.match(runner, /seed_running_local_checkpoint/u);
  assert.match(runner, /suspend_executor/u);
  assert.match(runner, /resume_executor/u);
  assert.match(runner, /stop_control_plane/u);
  assert.match(runner, /TaskStatus\.CANCELLED/u);
  assert.match(runner, /matching_executor_processes/u);
  assert.match(runner, /restart_count/u);
  assert.doesNotMatch(spec, /mock|localStorage/iu);
});
