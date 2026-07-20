import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const rustClient = await readFile("src-tauri/src/control_plane.rs", "utf8");
const rustEntry = await readFile("src-tauri/src/lib.rs", "utf8");
const source = await readFile("src/platform/tauri/task-target-preview-source.ts", "utf8");
const main = await readFile("src/main.tsx", "utf8");
const app = await readFile("src/app/App.tsx", "utf8");
const shell = await readFile("src/app/WorkbenchShell.tsx", "utf8");

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

test("the production App injects the fixed target preview source into task details", () => {
  assert.match(main, /new TauriTaskTargetPreviewSource\(\)/u);
  assert.match(main, /taskTargetPreviewSource=\{taskTargetPreviewSource\}/u);
  assert.match(app, /taskTargetPreviewSource=\{taskTargetPreviewSource\}/u);
  assert.match(shell, /taskTargetPreviewSource=\{taskTargetPreviewSource\}/u);
  assert.doesNotMatch(main, /TaskTargetPreviewSourceError|shellTaskTargetPreviewSource/u);
});

test("the D6-12 acceptance command only prepares data before the real UI calls production commands", () => {
  assert.match(rustEntry, /async fn prepare_task_target_preview_ui_for_acceptance/u);
  assert.match(rustEntry, /prepare_task_target_preview_ui_for_acceptance,/u);
});
