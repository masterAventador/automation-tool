import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, frontendRoot), "utf8");
}

test("E4-06 keeps the 256-bit local launch secret on the Rust-to-Python stdin boundary", async () => {
  const [cargo, rustEntry, rustBootstrap, pythonAuthentication, pythonBootstrap, pythonRuntime] =
    await Promise.all([
      readProjectFile("src-tauri/Cargo.toml"),
      readProjectFile("src-tauri/src/lib.rs"),
      readProjectFile("src-tauri/src/executor_bootstrap.rs"),
      readProjectFile("../backend/src/automation_tool/executor/authentication.py"),
      readProjectFile("../backend/src/automation_tool/executor/bootstrap.py"),
      readProjectFile("../backend/src/automation_tool/executor/runtime.py"),
    ]);

  assert.match(rustEntry, /pub mod executor_bootstrap;/);
  assert.match(cargo, /getrandom\s*=\s*"=0\.4\.3"/);
  assert.match(cargo, /hmac\s*=\s*\{[^}]*version\s*=\s*"=0\.13\.0"[^}]*"zeroize"/);
  assert.match(rustBootstrap, /LOCAL_SESSION_TOKEN_BYTES:\s*usize\s*=\s*32/);
  assert.match(rustBootstrap, /getrandom::fill/);
  assert.match(rustBootstrap, /verify_slice/);
  assert.match(rustBootstrap, /local_session_token/);
  assert.match(rustBootstrap, /write_all/);
  assert.match(rustBootstrap, /zeroize/);
  assert.doesNotMatch(rustBootstrap, /#\[tauri::command\]/);
  assert.doesNotMatch(rustBootstrap, /std::process::Command|\.args?\(|\.env\(|std::env::var/);
  assert.doesNotMatch(rustBootstrap, /println!|eprintln!|dbg!/);

  assert.match(pythonAuthentication, /hmac\.digest/);
  assert.match(pythonAuthentication, /atlep1\./);
  assert.match(pythonBootstrap, /local_session_token:\s*SecretStr/);
  assert.match(pythonRuntime, /"authenticationProof"/);
  assert.doesNotMatch(pythonRuntime, /local_session_token/);
});
