import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const rustClient = await readFile("src-tauri/src/control_plane.rs", "utf8");
const rustEntry = await readFile("src-tauri/src/lib.rs", "utf8");
const source = await readFile("src/platform/tauri/task-target-preview-source.ts", "utf8");

test("target preview crosses the App boundary only through fixed production commands", () => {
  for (const command of [
    "get_task_target_preview",
    "replace_task_target_exclusions",
    "confirm_task_target_preview",
  ]) {
    assert.match(rustEntry, new RegExp(`async fn ${command}`));
    assert.match(source, new RegExp(`"${command}"`));
  }
  assert.match(rustClient, /GetTaskTargetPreview/u);
  assert.match(rustClient, /ReplaceTaskTargetExclusions/u);
  assert.match(rustClient, /ConfirmTaskTargetPreview/u);
  assert.doesNotMatch(source, /fetch\(|axios|XMLHttpRequest/u);
  assert.doesNotMatch(source, /cookie|profilePath|platformTargetId|dedupeKey/iu);
});
