import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const rust = readFileSync(new URL("../src-tauri/src/lib.rs", import.meta.url), "utf8");

test("video editing exposes only the six fixed production Tauri commands", () => {
  for (const [command, clientMethod] of [
    ["list_editing_projects", "list_editing_projects"],
    ["create_editing_project", "create_editing_project"],
    ["get_editing_project_timeline", "get_editing_project_timeline"],
    ["save_editing_project_timeline", "save_editing_project_timeline"],
    ["list_editing_jobs", "list_editing_jobs"],
    ["submit_editing_job", "submit_editing_job"],
  ]) {
    assert.match(rust, new RegExp(`async fn ${command}\\(`));
    assert.match(rust, new RegExp(`\\.${clientMethod}\\(`));
    assert.equal(rust.match(new RegExp(`\\b${command}\\b`, "g"))?.length, 4);
  }
  assert.doesNotMatch(rust, /async fn invoke_editing_operation\(/);
});

test("plain desktop E2E cannot replace the production editing boundary", () => {
  const plainDesktopHandler = rust.match(
    /#\[cfg\(all\(not\(feature = "control-plane-e2e"\), feature = "desktop-e2e"\)\)\][\s\S]*?let builder = builder\.invoke_handler\(tauri::generate_handler!\[([\s\S]*?)\]\);/,
  )?.[1];
  assert.ok(plainDesktopHandler, "plain desktop E2E handler must remain explicit");
  for (const command of [
    "list_editing_projects",
    "create_editing_project",
    "get_editing_project_timeline",
    "save_editing_project_timeline",
    "list_editing_jobs",
    "submit_editing_job",
  ]) {
    assert.doesNotMatch(plainDesktopHandler, new RegExp(`\\b${command}\\b`));
  }
});
