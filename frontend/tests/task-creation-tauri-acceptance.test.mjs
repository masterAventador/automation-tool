import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, frontendRoot), "utf8");
}

test("Task creation acceptance uses one isolated hidden Tauri App path", async () => {
  const [packageJson, tauriConfig, wdioConfig, spec, rustEntry] =
    await Promise.all([
      readProjectFile("package.json"),
      readProjectFile("src-tauri/tauri.task-creation-e2e.conf.json"),
      readProjectFile("wdio.task-creation.conf.ts"),
      readProjectFile("e2e-tauri/task-creation.spec.ts"),
      readProjectFile("src-tauri/src/lib.rs"),
    ]);
  const orchestrator = await readFile(
    new URL("../../scripts/run_t3_06_acceptance.py", import.meta.url),
    "utf8",
  );

  assert.match(packageJson, /test:task-creation-tauri/);
  assert.match(packageJson, /build:tauri:task-creation-test/);
  assert.match(tauriConfig, /com\.aventador\.automationtool\.t306acceptance/);
  assert.match(tauriConfig, /"visible"\s*:\s*false/);
  assert.match(wdioConfig, /specs:\s*\["\.\/e2e-tauri\/task-creation\.spec\.ts"\]/);
  assert.doesNotMatch(wdioConfig, /\*\.spec/);
  assert.match(spec, /core\.invoke\("create_task_for_acceptance"\)/);
  assert.match(spec, /summary\.replayed, true/);
  assert.match(rustEntry, /\.create_task\(&vault, "task:create:tauri-acceptance"\)/);
  assert.match(orchestrator, /test:task-creation-tauri/);
  assert.match(orchestrator, /visible.*False|hidden Tauri App/);
});
