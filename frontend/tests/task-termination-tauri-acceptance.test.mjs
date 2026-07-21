import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, frontendRoot), "utf8");
}

test("cancel and emergency stop use the formal Rust bridge from one hidden App", async () => {
  const [packageJson, tauriConfigSource, wdioConfig, spec, rustClient, rustEntry] =
    await Promise.all([
      readProjectFile("package.json"),
      readProjectFile("src-tauri/tauri.task-termination-e2e.conf.json"),
      readProjectFile("wdio.task-termination.conf.ts"),
      readProjectFile("e2e-tauri/task-termination.spec.ts"),
      readProjectFile("src-tauri/src/control_plane.rs"),
      readProjectFile("src-tauri/src/lib.rs"),
    ]);
  const tauriConfig = JSON.parse(tauriConfigSource);
  const orchestrator = await readFile(
    new URL("../../scripts/run_t3_14_acceptance.py", import.meta.url),
    "utf8",
  );

  assert.match(packageJson, /test:task-termination-tauri/);
  assert.match(packageJson, /build:tauri:task-termination-test/);
  assert.equal(tauriConfig.app.windows.length, 1);
  assert.equal(tauriConfig.app.windows[0].visible, false);
  assert.equal(tauriConfig.identifier, "com.aventador.automationtool.t314acceptance");
  assert.match(wdioConfig, /specs:\s*\["\.\/e2e-tauri\/task-termination\.spec\.ts"\]/);
  assert.match(spec, /core\.invoke\("terminate_tasks_for_acceptance"\)/);
  assert.match(rustClient, /pub async fn cancel_task/);
  assert.match(rustClient, /pub async fn emergency_stop_task/);
  assert.match(rustEntry, /\.cancel_task\(\s*&vault/);
  assert.match(rustEntry, /\.emergency_stop_task\(\s*&vault/);
  assert.match(orchestrator, /test:task-termination-tauri/);
  assert.match(orchestrator, /seed_task_confirmation/);
  assert.match(orchestrator, /AUTOMATION_TOOL_TASK_TERMINATION_CONFIRMED_REVISION/);
  assert.match(orchestrator, /visible=false/);
});
