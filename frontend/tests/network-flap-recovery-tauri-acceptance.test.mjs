import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);
const repositoryRoot = new URL("../../", import.meta.url);

test("H8-07 owns one hidden App network-flap recovery boundary", async () => {
  const [packageJson, tauriConfig, wdioConfig, spec, runner, runtime, ledger, rustEntry] =
    await Promise.all([
      readFile(new URL("package.json", frontendRoot), "utf8"),
      readFile(new URL("src-tauri/tauri.network-recovery-e2e.conf.json", frontendRoot), "utf8"),
      readFile(new URL("wdio.network-recovery.conf.ts", frontendRoot), "utf8"),
      readFile(new URL("e2e-tauri/network-recovery.spec.ts", frontendRoot), "utf8"),
      readFile(new URL("scripts/run_h8_07_acceptance.py", repositoryRoot), "utf8"),
      readFile(
        new URL("backend/src/automation_tool/executor/runtime.py", repositoryRoot),
        "utf8",
      ),
      readFile(
        new URL("backend/src/automation_tool/executor/ledger.py", repositoryRoot),
        "utf8",
      ),
      readFile(new URL("src-tauri/src/lib.rs", frontendRoot), "utf8"),
    ]);
  const configuration = JSON.parse(tauriConfig);

  assert.match(packageJson, /build:tauri:network-recovery-test/u);
  assert.match(packageJson, /test:h8-07-tauri/u);
  assert.equal(configuration.identifier, "com.aventador.automationtool.h807acceptance");
  assert.equal(configuration.app.windows.length, 1);
  assert.equal(configuration.app.windows[0].visible, false);
  assert.match(wdioConfig, /e2e-tauri\/network-recovery\.spec\.ts/u);
  assert.match(spec, /prepare_network_recovery_for_acceptance/u);
  assert.match(spec, /restart_executor/u);
  assert.match(spec, /取消命令已提交/u);
  assert.match(spec, /Control Plane 不可用/u);
  assert.match(spec, /restartCount/u);
  assert.match(spec, /AUTOMATION_TOOL_H807_FACTS_VERIFIED_SIGNAL/u);
  assert.match(rustEntry, /prepare_network_recovery_for_acceptance/u);
  assert.match(runner, /build_signed_executor/u);
  assert.match(runner, /kill_control_plane_abruptly/u);
  assert.match(runner, /wait_for_transport_connected/u);
  assert.match(runner, /verify_dispatch_is_blocked/u);
  assert.match(runner, /facts-verified/u);
  assert.match(runner, /TaskStatus\.CANCELLED/u);
  assert.match(runner, /matching_executor_processes/u);
  assert.match(runtime, /_is_recoverable_transport_error/u);
  assert.match(ledger, /_MAX_PENDING_OUTBOX_ENTRIES/u);
  assert.match(ledger, /network_connected/u);
  assert.doesNotMatch(spec, /mock|localStorage/iu);
});
