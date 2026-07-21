import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const frontendRoot = new URL("../", import.meta.url);

test("H8-14 keeps workbench metrics on one read-only production path", async () => {
  const [rustClient, rustEntry, gateway, workbench] = await Promise.all([
    readFile(new URL("src-tauri/src/control_plane.rs", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/src/lib.rs", frontendRoot), "utf8"),
    readFile(new URL("src/features/workbench/workbench-gateway.ts", frontendRoot), "utf8"),
    readFile(new URL("src/features/workbench/Workbench.tsx", frontendRoot), "utf8"),
  ]);

  assert.match(rustClient, /GetWorkbenchMetrics/u);
  assert.match(rustClient, /\/api\/v1\/workbench\/metrics/u);
  assert.match(rustClient, /get_workbench_metrics/u);
  assert.match(rustEntry, /fn get_workbench_metrics/u);
  assert.match(gateway, /getMetrics/u);
  assert.match(gateway, /workbenchMetricsQueryOptions/u);
  assert.match(workbench, /当前需接管/u);
  assert.match(workbench, /动作结果待确认/u);
  assert.doesNotMatch(workbench, /function isToday/u);
});

test("H8-14 acceptance uses one hidden App and isolated PostgreSQL facts", async () => {
  const repositoryRoot = new URL("../", frontendRoot);
  const [configurationText, specification, runner, packageText] = await Promise.all([
    readFile(new URL("src-tauri/tauri.workbench-metrics-e2e.conf.json", frontendRoot), "utf8"),
    readFile(new URL("e2e-tauri/workbench-metrics.spec.ts", frontendRoot), "utf8"),
    readFile(new URL("scripts/run_h8_14_acceptance.py", repositoryRoot), "utf8"),
    readFile(new URL("package.json", frontendRoot), "utf8"),
  ]);
  const configuration = JSON.parse(configurationText);
  const packageJson = JSON.parse(packageText);

  assert.equal(configuration.identifier, "com.aventador.automationtool.h814acceptance");
  assert.equal(configuration.app.windows.length, 1);
  assert.equal(configuration.app.windows[0].visible, false);
  assert.match(specification, /browser\.tauri\.execute/u);
  assert.match(specification, /累计任务/u);
  assert.match(runner, /automation-tool-h814-/u);
  assert.match(runner, /postgres-test/u);
  assert.match(runner, /seed_metric_facts/u);
  assert.equal(
    packageJson.scripts["test:h8-14-tauri"],
    "../backend/.venv/bin/python ../scripts/run_h8_14_acceptance.py",
  );
});
