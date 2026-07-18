import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, frontendRoot), "utf8");
}

test("Task run acceptance clicks controls through one isolated hidden Tauri App", async () => {
  const [packageJson, tauriConfig, wdioConfig, spec, page, gateway, rustEntry] =
    await Promise.all([
      readProjectFile("package.json"),
      readProjectFile("src-tauri/tauri.task-run-e2e.conf.json"),
      readProjectFile("wdio.task-run.conf.ts"),
      readProjectFile("e2e-tauri/task-run.spec.ts"),
      readProjectFile("src/features/task-runs/TaskRunDetails.tsx"),
      readProjectFile("src/platform/tauri/task-run-control-gateway.ts"),
      readProjectFile("src-tauri/src/lib.rs"),
    ]);
  const orchestrator = await readFile(
    new URL("../../scripts/run_t3_18_acceptance.py", import.meta.url),
    "utf8",
  );

  assert.match(packageJson, /test:task-run-tauri/);
  assert.match(tauriConfig, /com\.aventador\.automationtool\.t318acceptance/);
  assert.match(tauriConfig, /"visible"\s*:\s*false/);
  assert.match(wdioConfig, /specs:\s*\["\.\/e2e-tauri\/task-run\.spec\.ts"\]/);
  assert.doesNotMatch(wdioConfig, /\*\.spec/);
  assert.match(spec, /clickTwoCharacterButton\("暂", "停"\)/u);
  assert.match(spec, /clickTwoCharacterButton\("恢", "复"\)/u);
  for (const label of ["取消任务", "紧急停止"]) {
    assert.match(spec, new RegExp(`button=${label}`));
  }
  assert.match(page, /taskSource\.streamTaskEvents/);
  assert.match(page, /afterSequence|previousSequence/);
  assert.match(gateway, /"pause_task_run"/);
  assert.match(rustEntry, /prepare_task_run_for_acceptance/);
  assert.match(orchestrator, /test:task-run-tauri/);
  assert.match(orchestrator, /visible=false/);
});
