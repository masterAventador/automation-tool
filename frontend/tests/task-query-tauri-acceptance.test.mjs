import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, frontendRoot), "utf8");
}

test("Task query acceptance uses one isolated hidden Tauri App path", async () => {
  const [packageJson, tauriConfig, wdioConfig, spec, rustEntry] = await Promise.all([
    readProjectFile("package.json"),
    readProjectFile("src-tauri/tauri.task-query-e2e.conf.json"),
    readProjectFile("wdio.task-query.conf.ts"),
    readProjectFile("e2e-tauri/task-query.spec.ts"),
    readProjectFile("src-tauri/src/lib.rs"),
  ]);
  const orchestrator = await readFile(
    new URL("../../scripts/run_t3_07_acceptance.py", import.meta.url),
    "utf8",
  );

  assert.match(packageJson, /test:task-query-tauri/);
  assert.match(packageJson, /build:tauri:task-query-test/);
  assert.match(tauriConfig, /com\.aventador\.automationtool\.t307acceptance/);
  assert.match(tauriConfig, /"visible"\s*:\s*false/);
  assert.match(wdioConfig, /specs:\s*\["\.\/e2e-tauri\/task-query\.spec\.ts"\]/);
  assert.doesNotMatch(wdioConfig, /\*\.spec/);
  assert.match(spec, /core\.invoke\("query_tasks_for_acceptance"\)/);
  assert.match(spec, /summary\.foreignHidden, true/);
  assert.match(rustEntry, /\.list_tasks\(&vault, None, 2\)/);
  assert.match(rustEntry, /\.get_task\(&vault, &foreign_task_id\)/);
  assert.match(orchestrator, /test:task-query-tauri/);
  assert.match(orchestrator, /require_hidden_tauri_configuration/);
});
