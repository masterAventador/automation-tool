import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const frontendRoot = new URL("../", import.meta.url);

test("H8-13 exports only a fixed local diagnostic package from an explicit App action", async () => {
  const [nativeExport, rustEntry, platformTypes, adapter, diagnostics, packageSource] =
    await Promise.all([
      readFile(new URL("src-tauri/src/diagnostic_export.rs", frontendRoot), "utf8"),
      readFile(new URL("src-tauri/src/lib.rs", frontendRoot), "utf8"),
      readFile(new URL("src/platform/types.ts", frontendRoot), "utf8"),
      readFile(new URL("src/platform/tauri/platform-adapter.ts", frontendRoot), "utf8"),
      readFile(new URL("src/features/diagnostics/Diagnostics.tsx", frontendRoot), "utf8"),
      readFile(new URL("package.json", frontendRoot), "utf8"),
    ]);

  assert.match(nativeExport, /DIAGNOSTIC_EXPORT_VERSION:\s*&str\s*=\s*"1"/u);
  assert.match(nativeExport, /artifacts\/evidence\/page-drift/u);
  assert.match(nativeExport, /artifacts\/diagnostics\/screenshots/u);
  assert.match(nativeExport, /artifacts\/diagnostics\/traces/u);
  assert.match(nativeExport, /ZipWriter/u);
  assert.match(nativeExport, /create_new\(true\)/u);
  assert.match(nativeExport, /MAX_DIAGNOSTIC_EXPORT_BYTES/u);
  assert.doesNotMatch(
    nativeExport,
    /executor-ledger\.sqlite3|browser-profiles|Cookie|comment_body|message_body|ControlPlane/u,
  );

  assert.match(rustEntry, /mod diagnostic_export;/u);
  assert.match(rustEntry, /fn export_diagnostics\s*\(/u);
  assert.match(rustEntry, /download_dir\(\)/u);
  assert.match(rustEntry, /export_diagnostics,/u);
  assert.match(platformTypes, /exportDiagnostics\(\): Promise<DiagnosticExportReceipt>/u);
  assert.match(adapter, /invoke<unknown>\("export_diagnostics"\)/u);
  assert.doesNotMatch(adapter, /export_diagnostics"\s*,\s*\{/u);
  assert.match(diagnostics, /导出诊断包/u);
  assert.match(diagnostics, /不会上传/u);
  assert.match(packageSource, /test:h8-13-tauri/u);
});

test("H8-13 keeps a hidden isolated real-App export acceptance", async () => {
  const [spec, runner, config] = await Promise.all([
    readFile(new URL("e2e-tauri/diagnostic-export.spec.ts", frontendRoot), "utf8"),
    readFile(new URL("../scripts/run_h8_13_acceptance.py", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/tauri.diagnostic-export-e2e.conf.json", frontendRoot), "utf8"),
  ]);

  assert.match(spec, /设置与诊断/u);
  assert.match(spec, /导出诊断包/u);
  assert.match(spec, /确认导出/u);
  assert.match(runner, /AUTOMATION_TOOL_H813_EXPORT_DIRECTORY/u);
  assert.match(runner, /diagnostic-export\.spec\.ts/u);
  assert.match(runner, /ZipFile/u);
  assert.match(config, /com\.aventador\.automationtool\.h813acceptance/u);
  assert.match(config, /"visible": false/u);
});
