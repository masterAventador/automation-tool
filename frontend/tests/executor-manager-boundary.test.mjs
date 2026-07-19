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
  assert.equal(JSON.parse(fixture).fixtureVersion, "1");
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

test("E4-12 consumes real offers only through the durable no-side-effect protocol path", async () => {
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
  assert.match(processor, /command\.message_type != "task\.offer"/);
  assert.match(processor, /commit_outcome\(/);
  assert.match(processor, /AttemptCheckpointState\.TERMINAL/);
  assert.match(runtime, /recover_outbox\(\)/);
  assert.match(runtime, /command_processor\.handle\(source\)/);
  assert.match(acceptance, /build_signed_executor/);
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
  ]) {
    assert.match(entry, new RegExp(`async fn ${command}|fn ${command}`));
    assert.match(tauriAdapter, new RegExp(`"${command}"`));
  }
  assert.match(entry, /app\.path\(\)\.app_data_dir\(\)/);
  assert.match(nativePlatform, /local-executor/);
  assert.match(nativePlatform, /executor-id-v1/);
  assert.match(platformTypes, /export interface PlatformAdapter/);
  assert.doesNotMatch(tauriAdapter, /https?:|wss?:|session|token|packageRoot|stateDirectory/i);
  assert.doesNotMatch(nativePlatform, /#\[tauri::command\]/);
});
