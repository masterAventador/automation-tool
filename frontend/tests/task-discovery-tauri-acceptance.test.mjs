import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, frontendRoot), "utf8");
}

test("discovery acceptance uses the production bridge from one hidden App", async () => {
  const [packageJson, configSource, wdio, spec, rustClient, rustEntry] =
    await Promise.all([
      readProjectFile("package.json"),
      readProjectFile("src-tauri/tauri.task-discovery-e2e.conf.json"),
      readProjectFile("wdio.task-discovery.conf.ts"),
      readProjectFile("e2e-tauri/task-discovery.spec.ts"),
      readProjectFile("src-tauri/src/control_plane.rs"),
      readProjectFile("src-tauri/src/lib.rs"),
    ]);
  const config = JSON.parse(configSource);
  const [orchestrator, controlledExecutor] = await Promise.all([
    readFile(
      new URL("../../scripts/run_d6_10_acceptance.py", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../../backend/tests/fixtures/d6_10_executor.py", import.meta.url),
      "utf8",
    ),
  ]);

  assert.match(packageJson, /test:task-discovery-tauri/u);
  assert.match(packageJson, /build:tauri:task-discovery-test/u);
  assert.equal(config.app.windows.length, 1);
  assert.equal(config.app.windows[0].visible, false);
  assert.equal(config.identifier, "com.aventador.automationtool.d610acceptance");
  assert.match(wdio, /specs:\s*\["\.\/e2e-tauri\/task-discovery\.spec\.ts"\]/u);
  assert.match(spec, /core\.invoke\("prepare_task_discovery_for_acceptance"\)/u);
  assert.match(spec, /button=开始目标发现/u);
  assert.match(spec, /发现目标中/u);
  assert.match(spec, /competingTaskId/u);
  assert.match(spec, /当前设备已有任务正在运行/u);
  assert.match(spec, /core\.invoke\("signal_task_discovery_busy_for_acceptance"\)/u);
  assert.match(rustClient, /pub async fn start_task_discovery/u);
  assert.match(rustEntry, /async fn start_task_discovery\(/u);
  const startDiscovery = rustEntry.match(/async fn start_task_discovery\([\s\S]*?\n\}\n/u);
  assert.ok(startDiscovery);
  assert.match(startDiscovery[0], /ensure_executor_running/u);
  assert.ok(
    startDiscovery[0].indexOf("ensure_executor_running") <
      startDiscovery[0].indexOf(".start_task_discovery"),
    "the signed Executor must be running before the discovery command is accepted",
  );
  const preparation = rustEntry.match(
    /async fn prepare_task_discovery_for_acceptance[\s\S]*?\n\}\n/u,
  );
  assert.ok(preparation);
  assert.doesNotMatch(preparation[0], /\.start_task_discovery\(/u);
  assert.match(orchestrator, /build_signed_executor/u);
  assert.match(orchestrator, /EXECUTOR_SPEC/u);
  assert.match(orchestrator, /install_executor_package/u);
  assert.match(orchestrator, /verify_controlled_executor/u);
  assert.match(orchestrator, /test:task-discovery-tauri/u);
  assert.match(orchestrator, /visible=false/u);
  assert.match(controlledExecutor, /AcceptanceDouyinDiscoveryOperation/u);
  assert.match(controlledExecutor, /ProductionDouyinDiscoveryOperation/u);
  assert.match(controlledExecutor, /discovery_waiting_for_busy_ui/u);
  assert.match(controlledExecutor, /busy_ui_observed/u);
  assert.ok(
    orchestrator.indexOf("build_signed_executor(") <
      orchestrator.indexOf('["pnpm", "test:task-discovery-tauri"]'),
    "the controlled signed package must be installed before the App starts its sidecar",
  );
  assert.doesNotMatch(orchestrator, /LocalExecutorProcess|ExecutorCommandProcessor|start_executor\(/u);
  assert.doesNotMatch(orchestrator, /sync_playwright|headless=false/iu);
});
