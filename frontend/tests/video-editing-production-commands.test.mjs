import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const rust = readFileSync(new URL("../src-tauri/src/lib.rs", import.meta.url), "utf8");

test("video editing exposes the six fixed project and job Tauri commands", () => {
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

test("local material import gets its private path only from the native picker", () => {
  const signature = rust.match(/async fn import_editing_material\(([\s\S]*?)\) ->/)?.[1];
  assert.ok(signature, "native material import command must exist");
  assert.doesNotMatch(signature, /source|path/i);
  assert.match(rust, /app\.dialog\(\)\.file\(\)\.blocking_pick_file\(\)/);
  assert.match(rust, /FilePath::Path\(source_path\)/);
  for (const command of [
    "import_editing_material",
    "get_local_editing_material_status",
    "get_local_editing_material_preview_url",
    "delete_editing_material",
  ]) {
    assert.match(rust, new RegExp(`async fn ${command}\\(`));
  }
  const previewSignature = rust.match(
    /async fn get_local_editing_material_preview_url\(([\s\S]*?)\) ->/,
  )?.[1];
  assert.ok(previewSignature, "native material preview command must exist");
  assert.match(previewSignature, /material_id/u);
  assert.doesNotMatch(previewSignature, /source|path|file_name/iu);
});

test("material library exposes path-free list and human-description commands", () => {
  for (const [command, clientMethod] of [
    ["list_editing_materials", "list_editing_materials"],
    ["update_editing_material_description", "update_editing_material_description"],
  ]) {
    assert.match(rust, new RegExp(`async fn ${command}\\(`));
    assert.match(rust, new RegExp(`\\.${clientMethod}\\(`));
    assert.equal(rust.match(new RegExp(`\\b${command}\\b`, "g"))?.length, 4);
  }
  const listSignature = rust.match(/async fn list_editing_materials\(([\s\S]*?)\) ->/)?.[1];
  assert.ok(listSignature, "native material list command must exist");
  assert.doesNotMatch(listSignature, /source|path|file_name/iu);
  const updateSignature = rust.match(
    /async fn update_editing_material_description\(([\s\S]*?)\) ->/,
  )?.[1];
  assert.ok(updateSignature, "native material description command must exist");
  assert.match(updateSignature, /material_id/u);
  assert.match(updateSignature, /description/u);
  assert.doesNotMatch(updateSignature, /source|path|file_name/iu);
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
    "list_editing_materials",
    "update_editing_material_description",
    "import_editing_material",
    "get_local_editing_material_status",
    "get_local_editing_material_preview_url",
    "delete_editing_material",
  ]) {
    assert.doesNotMatch(plainDesktopHandler, new RegExp(`\\b${command}\\b`));
  }
});
