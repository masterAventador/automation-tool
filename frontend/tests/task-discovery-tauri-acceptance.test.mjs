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
  const orchestrator = await readFile(
    new URL("../../scripts/run_d6_10_acceptance.py", import.meta.url),
    "utf8",
  );

  assert.match(packageJson, /test:task-discovery-tauri/u);
  assert.match(packageJson, /build:tauri:task-discovery-test/u);
  assert.equal(config.app.windows.length, 1);
  assert.equal(config.app.windows[0].visible, false);
  assert.equal(config.identifier, "com.aventador.automationtool.d610acceptance");
  assert.match(wdio, /specs:\s*\["\.\/e2e-tauri\/task-discovery\.spec\.ts"\]/u);
  assert.match(spec, /core\.invoke\("discover_task_for_acceptance"\)/u);
  assert.match(rustClient, /pub async fn start_task_discovery/u);
  assert.match(rustEntry, /\.start_task_discovery\(/u);
  assert.match(orchestrator, /LocalExecutorProcess/u);
  assert.match(orchestrator, /ExecutorCommandProcessor/u);
  assert.match(orchestrator, /test:task-discovery-tauri/u);
  assert.match(orchestrator, /visible=false/u);
  assert.doesNotMatch(orchestrator, /sync_playwright|headless=false/iu);
});
