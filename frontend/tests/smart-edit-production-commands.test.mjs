import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const rust = readFileSync(new URL("../src-tauri/src/lib.rs", import.meta.url), "utf8");
const runtime = readFileSync(
  new URL("../src-tauri/src/smart_edit_runtime.rs", import.meta.url),
  "utf8",
);

const COMMANDS = [
  "start_smart_edit_generation",
  "get_smart_edit_generation",
  "cancel_smart_edit_generation",
];

test("smart edit exposes three fixed path-free production commands", () => {
  for (const command of COMMANDS) {
    assert.match(rust, new RegExp(`fn ${command}\\(`));
    assert.equal(rust.match(new RegExp(`\\b${command}\\b`, "g"))?.length, 3);
    const signature = rust.match(new RegExp(`fn ${command}\\(([\\s\\S]*?)\\) ->`))?.[1];
    assert.ok(signature, `${command} signature must exist`);
    assert.doesNotMatch(signature, /path|source|api_key|transcript|result_document/iu);
  }
  assert.match(rust, /runtime\.start\(&app, request\)/u);
  assert.match(rust, /runtime\s*\.snapshot\(&generation_id\)/u);
  assert.match(rust, /runtime\.cancel\(&generation_id\)/u);
  assert.doesNotMatch(runtime, /#\[tauri::command\]/u);
});

test("only production-capable handlers expose smart edit", () => {
  const plainDesktopHandler = rust.match(
    /#\[cfg\(all\(not\(feature = "control-plane-e2e"\), feature = "desktop-e2e"\)\)\]\n {4}let builder = builder\.invoke_handler\(tauri::generate_handler!\[([\s\S]*?)\]\);/u,
  )?.[1];
  const productionHandler = rust.match(
    /#\[cfg\(all\(not\(feature = "control-plane-e2e"\), not\(feature = "desktop-e2e"\)\)\)\]\n {4}let builder = builder\.invoke_handler\(tauri::generate_handler!\[([\s\S]*?)\]\);/u,
  )?.[1];
  const controlPlaneHandler = rust.match(
    /#\[cfg\(feature = "control-plane-e2e"\)\]\n {4}let builder = builder\.invoke_handler\(tauri::generate_handler!\[([\s\S]*?)\]\);/u,
  )?.[1];
  assert.ok(plainDesktopHandler);
  assert.ok(productionHandler);
  assert.ok(controlPlaneHandler);
  for (const command of COMMANDS) {
    assert.doesNotMatch(plainDesktopHandler, new RegExp(`\\b${command}\\b`));
    assert.match(productionHandler, new RegExp(`\\b${command}\\b`));
    assert.match(controlPlaneHandler, new RegExp(`\\b${command}\\b`));
  }
  assert.equal(
    rust.match(/app\.manage\(smart_edit_runtime::SmartEditRuntime::new\(\)\)/gu)?.length,
    1,
  );
});

test("one-click render releases the material operation before nested dispatch", () => {
  const release = runtime.indexOf("drop(material_operation);");
  const dispatch = runtime.indexOf(
    "local_editing_runtime::dispatch_submitted_job(&app, &value)",
  );

  assert.ok(release >= 0, "smart edit must release its material operation explicitly");
  assert.ok(dispatch > release, "render dispatch must happen after the release");
});
