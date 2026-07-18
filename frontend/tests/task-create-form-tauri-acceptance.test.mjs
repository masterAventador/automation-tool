import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);
const readProjectFile = (path) => readFile(new URL(path, frontendRoot), "utf8");

test("Task form acceptance clicks the hidden App and uses the production Tauri command", async () => {
  const [packageJson, tauriConfig, wdioConfig, spec, form, gateway, rustEntry] =
    await Promise.all([
      readProjectFile("package.json"),
      readProjectFile("src-tauri/tauri.task-create-form-e2e.conf.json"),
      readProjectFile("wdio.task-create-form.conf.ts"),
      readProjectFile("e2e-tauri/task-create-form.spec.ts"),
      readProjectFile("src/features/task-create/TaskCreate.tsx"),
      readProjectFile("src/platform/tauri/task-creation-gateway.ts"),
      readProjectFile("src-tauri/src/lib.rs"),
    ]);
  const orchestrator = await readFile(
    new URL("../../scripts/run_t3_17_acceptance.py", import.meta.url),
    "utf8",
  );

  assert.match(packageJson, /test:task-create-form-tauri/);
  assert.match(tauriConfig, /com\.aventador\.automationtool\.t317acceptance/);
  assert.match(tauriConfig, /"visible"\s*:\s*false/);
  assert.match(wdioConfig, /specs:\s*\["\.\/e2e-tauri\/task-create-form\.spec\.ts"\]/);
  assert.doesNotMatch(wdioConfig, /\*\.spec/);
  assert.match(spec, /button=创建任务/);
  assert.match(spec, /#searchKeyword/);
  assert.match(form, /gateway\.createDouyinSearchExposureTask/);
  assert.match(gateway, /"create_douyin_search_exposure_task"/);
  assert.match(rustEntry, /async fn create_douyin_search_exposure_task/);
  assert.match(orchestrator, /test:task-create-form-tauri/);
  assert.match(orchestrator, /visible=false/);
  assert.doesNotMatch(spec, /fetch\(|axios|mock/i);
});
