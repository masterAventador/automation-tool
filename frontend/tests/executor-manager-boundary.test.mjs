import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

test("E4-07 keeps Executor lifecycle in a fixed Rust manager without restoring stdio tasks", async () => {
  const [entry, manager] = await Promise.all([
    readFile(new URL("src-tauri/src/lib.rs", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/src/executor_manager.rs", frontendRoot), "utf8"),
  ]);

  assert.match(entry, /pub mod executor_manager;/);
  assert.match(manager, /pub struct ExecutorManager/);
  assert.match(manager, /ExecutorPackageVerifier/);
  assert.match(manager, /LocalSessionToken/);
  assert.match(manager, /Command::new/);
  assert.match(manager, /ExecutorManagerState/);
  assert.doesNotMatch(manager, /serde_json::Value/);
  assert.doesNotMatch(manager, /session_token.*request|task\.request|capability_id/);
  assert.doesNotMatch(manager, /#\[tauri::command\]/);
  assert.doesNotMatch(manager, /std::env::var|https?:\/\//);
});

test("E4-07 acceptance uses the native macOS or Windows package identity", async () => {
  const acceptance = await readFile(
    new URL("../scripts/run_e4_07_acceptance.py", frontendRoot),
    "utf8",
  );

  assert.match(acceptance, /platform\.system\(\) not in \{"Darwin", "Windows"\}/);
  assert.match(acceptance, /\{"x86_64", "amd64"\}/);
  assert.match(
    acceptance,
    /"windows" if platform\.system\(\) == "Windows" else "macos"/,
  );  assert.match(acceptance, /"executor_manager_packaged"/);
  assert.match(acceptance, /"1 passed; 0 failed"/);

});

test("E4-08 supervises only the fixed Executor with an explicit bounded restart policy", async () => {
  const manager = await readFile(
    new URL("src-tauri/src/executor_manager.rs", frontendRoot),
    "utf8",
  );

  assert.match(manager, /ExecutorRestartPolicy/);
  assert.match(manager, /maximum_restarts/);
  assert.match(manager, /restart_count/);
  assert.match(manager, /std::thread::Builder/);
  assert.match(manager, /RestartPending/);
  assert.doesNotMatch(manager, /loop\s*\{[^}]*Command::new/s);
});

test("E4-08 acceptance crashes a real packaged Executor through the bounded Windows supervisor", async () => {
  const acceptance = await readFile(
    new URL("../scripts/run_e4_08_acceptance.py", frontendRoot),
    "utf8",
  );

  assert.match(acceptance, /"executor_manager_packaged"/);
  assert.match(acceptance, /"real_packaged_executor_enforces_bounded_restart_policy"/);
  assert.match(acceptance, /"control-plane-e2e"/);
  assert.match(acceptance, /"1 passed; 0 failed"/);
});

test("E4-09 isolates and terminates the complete Executor process tree on each platform", async () => {
  const [cargo, manager] = await Promise.all([
    readFile(new URL("src-tauri/Cargo.toml", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/src/executor_manager.rs", frontendRoot), "utf8"),
  ]);

  assert.match(manager, /process_group\(0\)/);
  assert.match(manager, /CREATE_SUSPENDED/);
  assert.match(manager, /JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE/);
  assert.match(manager, /TerminateJobObject/);
  assert.match(cargo, /Win32_System_JobObjects/);
  assert.match(cargo, /Win32_System_Threading/);
  assert.match(cargo, /Win32_System_Diagnostics_ToolHelp/);
});

test("E4-09 acceptance proves Windows Job Object cleanup with a real packaged descendant", async () => {
  const acceptance = await readFile(
    new URL("../scripts/run_e4_09_acceptance.py", frontendRoot),
    "utf8",
  );

  assert.match(acceptance, /executor_process_tree_probe\.py/);
  assert.match(acceptance, /cwd=workspace/);
  assert.match(acceptance, /"executor_manager_packaged"/);
  assert.match(acceptance, /"real_packaged_executor_cleans_its_windows_job_tree"/);
  assert.match(acceptance, /"control-plane-e2e"/);
  assert.match(acceptance, /"1 passed; 0 failed"/);
});

test("E4-10 bounds and redacts stderr before diagnostics leave the Rust manager", async () => {
  const [diagnostics, entry, manager, fixture] = await Promise.all([
    readFile(new URL("src-tauri/src/executor_diagnostics.rs", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/src/lib.rs", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/src/executor_manager.rs", frontendRoot), "utf8"),
    readFile(
      new URL("../contracts/fixtures/executor-diagnostics-v1.json", frontendRoot),
      "utf8",
    ),
  ]);

  assert.match(entry, /mod executor_diagnostics;/);
  assert.match(manager, /pub fn diagnostics/);
  assert.match(diagnostics, /MAX_RETAINED_DIAGNOSTIC_LINES:\s*usize\s*=\s*200/);
  assert.match(diagnostics, /MAX_RETAINED_DIAGNOSTIC_BYTES:\s*usize\s*=\s*64\s*\*\s*1024/);
  assert.match(diagnostics, /MAX_DIAGNOSTIC_LINE_BYTES:\s*usize\s*=\s*4096/);
  assert.match(diagnostics, /\[TRUNCATED\]/);
  assert.doesNotMatch(manager, /BufReader::new\(stderr\)\.lines\(\)/);
  assert.equal(JSON.parse(fixture).fixtureVersion, "2");
});

test("E4-10/H8-11 acceptance streams real packaged stderr through every diagnostic bound", async () => {
  const [acceptance, entry, manager, platform, spec] = await Promise.all([
    readFile(new URL("../scripts/run_e4_10_acceptance.py", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/src/lib.rs", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/src/executor_manager.rs", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/src/executor_platform.rs", frontendRoot), "utf8"),
    readFile(new URL("e2e-tauri/executor-lifecycle.spec.ts", frontendRoot), "utf8"),
  ]);

  assert.match(acceptance, /executor_diagnostics_probe\.py/);
  assert.match(acceptance, /cwd=workspace/);
  assert.match(acceptance, /"executor_manager_packaged"/);
  assert.match(acceptance, /platform\.system\(\) not in \{"Darwin", "Windows"\}/);
  assert.match(acceptance, /"real_packaged_executor_bounds_and_redacts_stderr"/);
  assert.match(acceptance, /"control-plane-e2e"/);
  assert.match(acceptance, /"1 passed; 0 failed"/);
  assert.match(manager, /inject_raw_diagnostic_for_acceptance/);
  assert.match(platform, /inject_raw_diagnostic_for_acceptance/);
  assert.match(entry, /inject_hostile_executor_diagnostics_for_acceptance/);
  assert.match(spec, /inject_hostile_executor_diagnostics_for_acceptance/);
  assert.match(spec, /page_content=\[REDACTED\]/);
});

test("E4-11 keeps the durable command ledger inside the private Executor state directory", async () => {
  const [bootstrap, cli, ledger, manager] = await Promise.all([
    readFile(new URL("src-tauri/src/executor_bootstrap.rs", frontendRoot), "utf8"),
    readFile(
      new URL("../backend/src/automation_tool/executor/cli.py", frontendRoot),
      "utf8",
    ),
    readFile(
      new URL("../backend/src/automation_tool/executor/ledger.py", frontendRoot),
      "utf8",
    ),
    readFile(new URL("src-tauri/src/executor_manager.rs", frontendRoot), "utf8"),
  ]);

  assert.match(bootstrap, /state_directory/);
  assert.match(manager, /state_directory:\s*PathBuf/);
  assert.match(cli, /ExecutorLedger\(/);
  assert.ok(cli.indexOf("ExecutorLedger(") < cli.indexOf("LocalExecutorProcess("));
  assert.match(ledger, /EXECUTOR_LEDGER_FILE_NAME.*executor-ledger\.sqlite3/);
  assert.match(ledger, /PRAGMA user_version = 1/);
  for (const table of [
    "executor_identity",
    "executor_commands",
    "executor_attempt_checkpoints",
    "executor_outbox",
  ]) {
    assert.match(ledger, new RegExp(`CREATE TABLE ${table}`));
  }
  assert.doesNotMatch(ledger, /keyring|keychain|session_token|cookie|password/i);
  assert.doesNotMatch(ledger, /control_plane|FakeExecutor|:memory:/);
});

test("E4-12 consumes real offers and closed controls through the durable protocol path", async () => {
  const [cli, processor, runtime, acceptance] = await Promise.all([
    readFile(
      new URL("../backend/src/automation_tool/executor/cli.py", frontendRoot),
      "utf8",
    ),
    readFile(
      new URL(
        "../backend/src/automation_tool/executor/command_processor.py",
        frontendRoot,
      ),
      "utf8",
    ),
    readFile(
      new URL("../backend/src/automation_tool/executor/runtime.py", frontendRoot),
      "utf8",
    ),
    readFile(
      new URL("../scripts/run_e4_12_acceptance.py", frontendRoot),
      "utf8",
    ),
  ]);

  assert.match(cli, /ExecutorCommandProcessor\(/);
  assert.match(
    processor,
    /not in \{\s*"task\.offer",\s*"task\.pause",\s*"task\.resume",\s*"task\.cancel",\s*"task\.emergency_stop",\s*\}/,
  );
  assert.match(processor, /commit_outcome\(/);
  assert.match(processor, /AttemptCheckpointState\.TERMINAL/);
  assert.match(runtime, /recover_outbox\(\)/);
  assert.match(runtime, /command_processor\.handle\(source\)/);
  assert.match(acceptance, /build_signed_executor/);
  assert.match(acceptance, /platform\.system\(\) not in \{"Darwin", "Windows"\}/);
  assert.match(acceptance, /managed_test_postgres/);
  assert.match(acceptance, /closing\(sqlite3\.connect/);
  assert.match(acceptance, /"executor_manager_packaged"/);
  assert.match(acceptance, /"1 passed; 0 failed"/);
  assert.match(acceptance, /wait_for_convergence/);
  assert.match(acceptance, /Restarting against the same ledger for exact replay/);
  assert.doesNotMatch(processor, /FakeExecutor|playwright|selenium|keyring|keychain/i);
  assert.doesNotMatch(runtime, /#\[tauri::command\]/);
});

test("E4-13 exposes only fixed Executor lifecycle Commands through PlatformAdapter", async () => {
  const [entry, nativePlatform, platformTypes, tauriAdapter] = await Promise.all([
    readFile(new URL("src-tauri/src/lib.rs", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/src/executor_platform.rs", frontendRoot), "utf8"),
    readFile(new URL("src/platform/types.ts", frontendRoot), "utf8"),
    readFile(
      new URL("src/platform/tauri/platform-adapter.ts", frontendRoot),
      "utf8",
    ),
  ]);

  for (const command of [
    "get_executor_status",
    "restart_executor",
    "get_executor_diagnostics",
    "emergency_stop_executor",
    "get_browser_diagnostic_settings",
    "set_capture_successful_diagnostics",
  ]) {
    assert.match(entry, new RegExp(`async fn ${command}|fn ${command}`));
    assert.match(tauriAdapter, new RegExp(`"${command}"`));
  }
  assert.match(entry, /app\.path\(\)\.app_data_dir\(\)/);
  assert.match(nativePlatform, /local-executor/);
  assert.match(nativePlatform, /executor-id-v1/);
  assert.match(nativePlatform, /browser-diagnostic-settings-v1/);
  assert.match(nativePlatform, /with_capture_successful_diagnostics/);
  assert.match(platformTypes, /export interface PlatformAdapter/);
  assert.doesNotMatch(tauriAdapter, /https?:|wss?:|session|token|packageRoot|stateDirectory/i);
  assert.doesNotMatch(nativePlatform, /#\[tauri::command\]/);
});

test("E4-14 drives the signed Executor lifecycle through one isolated hidden App", async () => {
  const [
    packageJson,
    tauriConfig,
    wdioConfig,
    spec,
    orchestrator,
    controlPlane,
    executorPlatform,
    entry,
  ] =
    await Promise.all([
      readFile(new URL("package.json", frontendRoot), "utf8"),
      readFile(
        new URL("src-tauri/tauri.executor-lifecycle-e2e.conf.json", frontendRoot),
        "utf8",
      ),
      readFile(new URL("wdio.executor-lifecycle.conf.ts", frontendRoot), "utf8"),
      readFile(new URL("e2e-tauri/executor-lifecycle.spec.ts", frontendRoot), "utf8"),
      readFile(new URL("../scripts/run_e4_14_acceptance.py", frontendRoot), "utf8"),
      readFile(new URL("src-tauri/src/control_plane.rs", frontendRoot), "utf8"),
      readFile(new URL("src-tauri/src/executor_platform.rs", frontendRoot), "utf8"),
      readFile(new URL("src-tauri/src/lib.rs", frontendRoot), "utf8"),
    ]);

  assert.match(packageJson, /test:executor-lifecycle-tauri/);
  assert.match(packageJson, /build:tauri:executor-lifecycle-test/);
  assert.match(tauriConfig, /"visible": false/);
  assert.match(tauriConfig, /com\.aventador\.automationtool\.e414acceptance/);
  assert.match(wdioConfig, /executor-lifecycle\.spec\.ts/);
  assert.match(spec, /button=启动执行器/);
  assert.match(spec, /button=本地紧急停止/);
  assert.match(spec, /inject_executor_crash_for_acceptance/);
  assert.match(spec, /inject_executor_hang_for_acceptance/);
  assert.match(spec, /stoppedOrTimedOut/);
  assert.match(spec, /本地执行器已停止/);
  assert.match(spec, /暂时无法读取本地执行器状态。请稍后重试。/);
  assert.doesNotMatch(spec, /process\.platform/);
  assert.match(orchestrator, /build_signed_executor/);
  assert.match(orchestrator, /managed_test_postgres/);
  assert.match(orchestrator, /"pnpm.cmd" if sys.platform == "win32" else "pnpm"/);
  assert.match(orchestrator, /closing\(sqlite3\.connect/);
  assert.match(orchestrator, /!= \(7,\):/);
  assert.match(orchestrator, /browser-diagnostic-settings-v1/);
  assert.match(orchestrator, /automation-tool-e414-/);
  assert.match(orchestrator, /require_port_available/);
  assert.match(orchestrator, /assert_no_executor_process/);
  assert.doesNotMatch(orchestrator, /graceful_app_exit_observed/);
  assert.match(orchestrator, /executor-ledger\.sqlite3/);
  assert.match(controlPlane, /AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN/);
  assert.match(spec, /exit_app_for_acceptance/);
  assert.match(entry, /fn exit_app_for_acceptance/);
  assert.match(entry, /RunEvent::ExitRequested/);
  assert.match(entry, /shutdown_for_app_exit/);
  assert.match(executorPlatform, /EXECUTOR_START_TIMEOUT_SECONDS:\s*u64\s*=\s*30/);
  assert.match(
    executorPlatform,
    /Duration::from_secs\(EXECUTOR_START_TIMEOUT_SECONDS\)/,
  );
  assert.doesNotMatch(spec, /get_executor_status|restart_executor|get_executor_diagnostics|emergency_stop_executor/);
});
