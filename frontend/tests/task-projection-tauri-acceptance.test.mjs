import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, frontendRoot), "utf8");
}

test("Task projection acceptance uses Query and Tauri Channel from one hidden App", async () => {
  const [
    packageJson,
    tauriConfig,
    wdioConfig,
    spec,
    testRunner,
    source,
    reducer,
    rustClient,
    rustEntry,
  ] = await Promise.all([
    readProjectFile("package.json"),
    readProjectFile("src-tauri/tauri.task-projection-e2e.conf.json"),
    readProjectFile("wdio.task-projection.conf.ts"),
    readProjectFile("e2e-tauri/task-projection.spec.ts"),
    readProjectFile("src/test-task-projection-acceptance.ts"),
    readProjectFile("src/platform/tauri/task-projection-source.ts"),
    readProjectFile("src/features/task-runs/task-projection-reducer.ts"),
    readProjectFile("src-tauri/src/control_plane.rs"),
    readProjectFile("src-tauri/src/lib.rs"),
  ]);
  const orchestrator = await readFile(
    new URL("../../scripts/run_t3_15_acceptance.py", import.meta.url),
    "utf8",
  );

  assert.match(packageJson, /test:task-projection-tauri/);
  assert.match(packageJson, /build:tauri:task-projection-test/);
  assert.match(tauriConfig, /com\.aventador\.automationtool\.t315acceptance/);
  assert.match(tauriConfig, /"visible"\s*:\s*false/);
  assert.match(wdioConfig, /specs:\s*\["\.\/e2e-tauri\/task-projection\.spec\.ts"\]/);
  assert.doesNotMatch(wdioConfig, /\*\.spec/);
  assert.match(spec, /__automationToolTaskProjectionAcceptance/);
  assert.match(testRunner, /followTaskProjection/);
  assert.match(testRunner, /new TauriTaskProjectionSource/);
  assert.match(source, /new Channel<unknown>/);
  assert.match(source, /"get_task_snapshot"/);
  assert.match(source, /"stream_task_projection_events"/);
  assert.match(reducer, /event\.taskStatus/);
  assert.match(reducer, /lastEventSequence/);
  assert.match(rustClient, /pub async fn stream_task_events_with/);
  assert.match(rustEntry, /tauri::ipc::Channel<control_plane::TaskEvent>/);
  assert.match(orchestrator, /test:task-projection-tauri/);
  assert.match(orchestrator, /require_hidden_tauri_configuration/);
});
