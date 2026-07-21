import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

test("Task discovery has one App-session protected production Tauri command", () => {
  const rustEntry = read("../src-tauri/src/lib.rs");
  const client = read("../src-tauri/src/control_plane.rs");
  const api = read("../src/api/generated/control-plane.ts");
  const main = read("../src/main.tsx");
  const details = read("../src/features/task-runs/TaskRunDetails.tsx");
  const gateway = read("../src/platform/tauri/task-discovery-gateway.ts");

  assert.match(rustEntry, /async fn start_task_discovery/u);
  assert.match(rustEntry, /\.start_task_discovery\(&vault, &task_id, &idempotency_key\)/u);
  assert.match(client, /StartTaskDiscovery/u);
  assert.match(client, /\/api\/v1\/tasks\/\{task_id\}\/discoveries/u);
  assert.match(client, /DeviceSessionCapability::AppControlPlane/u);
  assert.match(client, /Some\(idempotency_key\)/u);
  assert.match(api, /startTaskDiscovery/u);
  assert.match(gateway, /invoke<unknown>\("start_task_discovery"/u);
  assert.match(main, /new TauriTaskDiscoveryGateway\(\)/u);
  assert.match(details, /discoveryGateway\.startDiscovery/u);
  assert.match(details, /开始目标发现/u);

  const discoveryMethod = client.match(
    /pub async fn start_task_discovery[\s\S]*?\n {4}pub async fn pause_task/u,
  );
  assert.ok(discoveryMethod);
  assert.doesNotMatch(
    discoveryMethod[0],
    /cookie|browser_profile|profile_path|page_text/iu,
  );
});
