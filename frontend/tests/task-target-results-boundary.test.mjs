import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

const root = new URL("../", import.meta.url);

function source(path) {
  return readFileSync(new URL(path, root), "utf8");
}

test("A7-15 exposes one fixed target-result path from hidden App to the existing run details", () => {
  const apiPath = "src/api/control-plane/task-target-results.ts";
  const tauriPath = "src/platform/tauri/task-target-result-source.ts";
  assert.equal(existsSync(new URL(apiPath, root)), true);
  assert.equal(existsSync(new URL(tauriPath, root)), true);

  const api = source(apiPath);
  const tauri = source(tauriPath);
  const details = source("src/features/task-runs/TaskRunDetails.tsx");
  const main = source("src/main.tsx");
  const rustClient = source("src-tauri/src/control_plane.rs");
  const rustCommands = source("src-tauri/src/lib.rs");
  const hiddenAppSpec = source("e2e-tauri/task-run.spec.ts");
  const hiddenAppConfig = source("src-tauri/tauri.task-run-e2e.conf.json");
  const orchestrator = source("../scripts/run_t3_18_acceptance.py");

  for (const status of ["succeeded", "skipped", "failed", "outcome_uncertain"]) {
    assert.match(api, new RegExp(`\\"${status}\\"`));
  }
  assert.match(tauri, /get_task_target_results/);
  assert.match(details, /taskTargetResultSource/);
  assert.match(details, /证据摘要/);
  assert.match(main, /TauriTaskTargetResultSource/);
  assert.match(rustClient, /\/api\/v1\/tasks\/\{task_id\}\/target-results/);
  assert.match(rustCommands, /get_task_target_results/);
  assert.match(hiddenAppConfig, /"visible"\s*:\s*false/);
  assert.match(orchestrator, /seed_target_results/);
  for (const evidence of [
    "平台页面已确认评论成功",
    "用户在预览中排除此目标",
    "平台登录状态需要人工处理",
    "已发送，但平台最终状态无法确认",
  ]) {
    assert.match(hiddenAppSpec, new RegExp(evidence));
  }
  assert.doesNotMatch(tauri, /https?:\/\/|authorization|bearer/i);
});
