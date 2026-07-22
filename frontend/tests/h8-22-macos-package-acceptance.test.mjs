import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

test("H8-22 owns an isolated ad-hoc macOS package acceptance", async () => {
  const [manifest, config, runner, spec] = await Promise.all([
    readFile(new URL("package.json", frontendRoot), "utf8"),
    readFile(
      new URL("src-tauri/tauri.update-macos-package-e2e.conf.json", frontendRoot),
      "utf8",
    ),
    readFile(new URL("../scripts/run_h8_22_macos_package_acceptance.py", frontendRoot), "utf8"),
    readFile(new URL("e2e-tauri/update-macos-package.spec.ts", frontendRoot), "utf8"),
  ]);

  assert.match(manifest, /test:h8-22-macos-package/u);
  assert.match(config, /com\.aventador\.automationtool\.h822macacceptance/u);
  assert.match(config, /"visible": false/u);
  assert.match(config, /"createUpdaterArtifacts": true/u);
  assert.match(config, /"signingIdentity": "-"/u);
  assert.match(runner, /tauri["'],\s*["']build/u);
  assert.match(runner, /["']dmg["']/u);
  assert.match(runner, /codesign/u);
  assert.match(runner, /CFBundleShortVersionString/u);
  assert.match(runner, /dir="\/private\/tmp"/u);
  assert.match(runner, /signature\.read_text\(encoding="utf-8"\)\.strip\(\)/u);
  assert.doesNotMatch(runner, /b64encode\(signature\.read_bytes/u);
  assert.match(runner, /optional-decisions/u);
  assert.match(runner, /optional-install/u);
  assert.match(runner, /scenario != "optional-install"/u);
  assert.match(runner, /wait_for_binary_hash/u);
  assert.match(runner, /runtime_environment\.pop\(name, None\)/u);
  assert.match(runner, /forced-first/u);
  assert.match(runner, /forced-reopen/u);
  assert.match(runner, /installer-failure/u);
  assert.match(runner, /"AUTOMATION_TOOL_UPDATE_INSTALL_PROBE"/u);
  assert.doesNotMatch(
    runner,
    /environment\["AUTOMATION_TOOL_UPDATE_INSTALL_PROBE"\]\s*=/u,
  );
  assert.match(spec, /clickEnabledButton\("稍后提醒"\)/u);
  assert.match(spec, /clickEnabledButton\("跳过此版本"\)/u);
  assert.match(spec, /clickEnabledButton\("立即安装"\)/u);
  assert.match(spec, /ECONNREFUSED/u);
  assert.match(spec, /waitForInstalledBinary\(\)/u);
  assert.match(spec, /installation_failed/u);
});
