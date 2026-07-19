import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, frontendRoot), "utf8");
}

test("T3-19 retains a hidden production-path lifecycle acceptance", async () => {
  const [packageJson, tauriConfig, wdioConfig, spec, rustEntry] = await Promise.all([
    readProjectFile("package.json"),
    readProjectFile("src-tauri/tauri.task-lifecycle-e2e.conf.json"),
    readProjectFile("wdio.task-lifecycle.conf.ts"),
    readProjectFile("e2e-tauri/task-lifecycle.spec.ts"),
    readProjectFile("src-tauri/src/lib.rs"),
  ]);
  const orchestrator = await readFile(
    new URL("../../scripts/run_t3_19_acceptance.py", import.meta.url),
    "utf8",
  );

  assert.match(packageJson, /test:task-lifecycle-tauri/);
  assert.match(tauriConfig, /com\.aventador\.automationtool\.t319acceptance/);
  assert.match(tauriConfig, /"visible"\s*:\s*false/u);
  assert.match(wdioConfig, /e2e-tauri\/task-lifecycle\.spec\.ts/u);
  for (const fact of ["创建任务", "任务已暂停", "任务已恢复", "任务已取消", "任务完成"]) {
    assert.match(spec, new RegExp(fact));
  }
  assert.match(spec, /browser\.refresh\(\)/u);
  assert.match(rustEntry, /prepare_task_lifecycle_for_acceptance/u);
  assert.match(orchestrator, /FakeExecutorScenario\.HOLD/u);
  assert.match(orchestrator, /FakeExecutorScenario\.SUCCEED/u);
});
