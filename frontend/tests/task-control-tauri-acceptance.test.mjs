import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, frontendRoot), "utf8");
}

test("pause and resume acceptance uses the formal Rust bridge from one hidden App", async () => {
  const [packageJson, tauriConfigSource, wdioConfig, spec, rustClient, rustEntry] =
    await Promise.all([
      readProjectFile("package.json"),
      readProjectFile("src-tauri/tauri.task-control-e2e.conf.json"),
      readProjectFile("wdio.task-control.conf.ts"),
      readProjectFile("e2e-tauri/task-control.spec.ts"),
      readProjectFile("src-tauri/src/control_plane.rs"),
      readProjectFile("src-tauri/src/lib.rs"),
    ]);
  const tauriConfig = JSON.parse(tauriConfigSource);
  const orchestrator = await readFile(
    new URL("../../scripts/run_t3_13_acceptance.py", import.meta.url),
    "utf8",
  );

  assert.match(packageJson, /test:task-control-tauri/);
  assert.match(packageJson, /build:tauri:task-control-test/);
  assert.equal(tauriConfig.app.windows.length, 1);
  assert.equal(tauriConfig.app.windows[0].visible, false);
  assert.equal(tauriConfig.identifier, "com.aventador.automationtool.t313acceptance");
  assert.match(wdioConfig, /specs:\s*\["\.\/e2e-tauri\/task-control\.spec\.ts"\]/);
  assert.match(spec, /core\.invoke\("control_task_for_acceptance"\)/);
  assert.match(rustClient, /pub async fn pause_task/);
  assert.match(rustClient, /pub async fn resume_task/);
  assert.match(rustClient, /pub async fn cancel_task/);
  assert.match(rustClient, /pub async fn emergency_stop_task/);
  assert.match(rustEntry, /\.pause_task\(\s*&vault/);
  assert.match(rustEntry, /\.resume_task\(\s*&vault/);
  assert.match(orchestrator, /test:task-control-tauri/);
  assert.match(orchestrator, /visible=false/);
});
