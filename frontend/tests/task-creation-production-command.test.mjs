import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

test("Douyin Task creation has one fixed production Tauri Command", () => {
  const gateway = read("../src/platform/tauri/task-creation-gateway.ts");
  const rust = read("../src-tauri/src/lib.rs");
  const main = read("../src/main.tsx");

  assert.match(gateway, /invoke\("create_douyin_search_exposure_task"/);
  assert.doesNotMatch(gateway, /fetch\(|axios|Authorization|Bearer|https?:\/\//);
  assert.match(rust, /async fn create_douyin_search_exposure_task/);
  assert.match(main, /new TauriTaskCreationGateway\(\)/);
});
