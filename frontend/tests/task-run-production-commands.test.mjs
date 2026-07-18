import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const rust = readFileSync(new URL("../src-tauri/src/lib.rs", import.meta.url), "utf8");

test("Task run details exposes four fixed production Tauri commands", () => {
  for (const [command, clientMethod] of [
    ["pause_task_run", "pause_task"],
    ["resume_task_run", "resume_task"],
    ["cancel_task_run", "cancel_task"],
    ["emergency_stop_task_run", "emergency_stop_task"],
  ]) {
    assert.match(rust, new RegExp(`async fn ${command}\\(`));
    assert.match(rust, new RegExp(`\\.${clientMethod}\\(`));
    assert.ok(rust.match(new RegExp(`\\b${command}\\b`, "g"))?.length >= 3);
  }
  assert.doesNotMatch(rust, /async fn control_task_run\(/);
});
