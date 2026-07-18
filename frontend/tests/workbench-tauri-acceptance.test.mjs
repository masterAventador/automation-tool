import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, frontendRoot), "utf8");
}

test("workbench acceptance clicks the real hidden App UI and formal gateway", async () => {
  const [packageJson, tauriConfig, wdioConfig, spec, workbench, gateway, rustEntry] =
    await Promise.all([
      readProjectFile("package.json"),
      readProjectFile("src-tauri/tauri.workbench-e2e.conf.json"),
      readProjectFile("wdio.workbench.conf.ts"),
      readProjectFile("e2e-tauri/workbench-control.spec.ts"),
      readProjectFile("src/features/workbench/Workbench.tsx"),
      readProjectFile("src/platform/tauri/workbench-gateway.ts"),
      readProjectFile("src-tauri/src/lib.rs"),
    ]);
  const orchestrator = await readFile(
    new URL("../../scripts/run_t3_16_acceptance.py", import.meta.url),
    "utf8",
  );

  assert.match(packageJson, /test:workbench-tauri/);
  assert.match(tauriConfig, /com\.aventador\.automationtool\.t316acceptance/);
  assert.match(tauriConfig, /"visible"\s*:\s*false/);
  assert.match(
    wdioConfig,
    /specs:\s*\["\.\/e2e-tauri\/workbench-control\.spec\.ts"\]/,
  );
  assert.doesNotMatch(wdioConfig, /\*\.spec/);
  assert.match(spec, /button=全局紧急停止/);
  assert.match(spec, /button=确认紧停/);
  assert.match(workbench, /gateway\.emergencyStopTask/);
  assert.match(gateway, /"emergency_stop_workbench_task"/);
  assert.match(rustEntry, /async fn emergency_stop_workbench_task/);
  assert.match(orchestrator, /test:workbench-tauri/);
  assert.match(orchestrator, /visible=false/);
});
