import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, frontendRoot), "utf8");
}

test("Task event SSE acceptance uses one isolated hidden Tauri App path", async () => {
  const [packageJson, tauriConfig, wdioConfig, spec, rustClient, rustEntry] = await Promise.all([
    readProjectFile("package.json"),
    readProjectFile("src-tauri/tauri.task-event-stream-e2e.conf.json"),
    readProjectFile("wdio.task-event-stream.conf.ts"),
    readProjectFile("e2e-tauri/task-event-stream.spec.ts"),
    readProjectFile("src-tauri/src/control_plane.rs"),
    readProjectFile("src-tauri/src/lib.rs"),
  ]);
  const orchestrator = await readFile(
    new URL("../../scripts/run_t3_12_acceptance.py", import.meta.url),
    "utf8",
  );

  assert.match(packageJson, /test:task-event-stream-tauri/);
  assert.match(packageJson, /build:tauri:task-event-stream-test/);
  assert.match(tauriConfig, /com\.aventador\.automationtool\.t312acceptance/);
  assert.match(tauriConfig, /"visible"\s*:\s*false/);
  assert.match(wdioConfig, /specs:\s*\["\.\/e2e-tauri\/task-event-stream\.spec\.ts"\]/);
  assert.doesNotMatch(wdioConfig, /\*\.spec/);
  assert.match(spec, /core\.invoke\("stream_task_events_for_acceptance"\)/);
  assert.match(spec, /summary\.resumedSequences/);
  assert.match(rustClient, /pub async fn stream_task_events/);
  assert.match(rustClient, /"text\/event-stream"/);
  assert.match(rustEntry, /\.stream_task_events\(&vault/);
  assert.match(orchestrator, /test:task-event-stream-tauri/);
  assert.match(orchestrator, /require_hidden_tauri_configuration/);
});
