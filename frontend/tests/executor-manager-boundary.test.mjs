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
