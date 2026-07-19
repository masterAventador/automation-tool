import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, frontendRoot), "utf8");
}

test("T3-20 retains a hidden production-path restart acceptance", async () => {
  const [packageJson, tauriConfig, wdioConfig, spec, rustEntry] = await Promise.all([
    readProjectFile("package.json"),
    readProjectFile("src-tauri/tauri.task-restart-e2e.conf.json"),
    readProjectFile("wdio.task-restart.conf.ts"),
    readProjectFile("e2e-tauri/task-restart.spec.ts"),
    readProjectFile("src-tauri/src/lib.rs"),
  ]);
  const orchestrator = await readFile(
    new URL("../../scripts/run_t3_20_acceptance.py", import.meta.url),
    "utf8",
  );

  assert.match(packageJson, /test:task-restart-tauri/u);
  assert.match(tauriConfig, /com\.aventador\.automationtool\.t320acceptance/u);
  assert.match(tauriConfig, /"visible"\s*:\s*false/u);
  assert.match(wdioConfig, /e2e-tauri\/task-restart\.spec\.ts/u);
  for (const fact of ["运行中", "Control Plane 不可用", "已取消", "任务已取消"]) {
    assert.match(spec, new RegExp(fact));
  }
  assert.match(spec, /browser\.refresh\(\)/u);
  assert.match(rustEntry, /prepare_task_restart_for_acceptance/u);
  assert.match(orchestrator, /run_reconnecting/u);
  assert.match(orchestrator, /Stopping the Control Plane/u);
  assert.match(orchestrator, /Restarting the Control Plane/u);
});
