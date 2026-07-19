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
