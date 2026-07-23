import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

test("H8-22 owns an isolated unsigned Windows NSIS package acceptance", async () => {
  const [manifestText, configText, runner, spec, wdio, gitignore] = await Promise.all([
    readFile(new URL("package.json", frontendRoot), "utf8"),
    readFile(
      new URL("src-tauri/tauri.update-windows-package-e2e.conf.json", frontendRoot),
      "utf8",
    ),
    readFile(
      new URL("../scripts/run_h8_22_windows_package_acceptance.py", frontendRoot),
      "utf8",
    ),
    readFile(new URL("e2e-tauri/update-windows-package.spec.ts", frontendRoot), "utf8"),
    readFile(new URL("wdio.update-windows-package.conf.ts", frontendRoot), "utf8"),
    readFile(new URL("../.gitignore", frontendRoot), "utf8"),
  ]);
  const manifest = JSON.parse(manifestText);
  const config = JSON.parse(configText);

  assert.equal(
    manifest.scripts["test:h8-22-windows-package"],
    "uv run --project ../backend --locked python ../scripts/run_h8_22_windows_package_acceptance.py",
  );
  assert.equal(
    config.identifier,
    "com.aventador.automationtool.h822windowsacceptance",
  );
  assert.equal(config.productName, "Automation Tool H822 Windows Acceptance");
  assert.equal(config.app.windows.length, 1);
  assert.equal(config.app.windows[0].visible, false);
  assert.deepEqual(config.bundle.targets, ["nsis"]);
  assert.equal(config.bundle.createUpdaterArtifacts, true);
  assert.equal(config.bundle.windows.nsis.installMode, "currentUser");
  assert.equal(config.plugins.updater.windows.installMode, "passive");

  assert.match(runner, /sys\.platform != "win32"/u);
  assert.match(runner, /require_non_elevated_process/u);
  assert.match(runner, /["']--bundles["'],\s*["']nsis["']/u);
  assert.match(runner, /signature\.read_text\(encoding="utf-8"\)\.strip\(\)/u);
  assert.doesNotMatch(runner, /b64encode\(signature\.read_bytes/u);
  assert.match(runner, /verify_unsigned_installer/u);
  assert.match(runner, /\[Console\]::In\.ReadToEnd\(\)/u);
  assert.match(runner, /hasSigner/u);
  assert.match(runner, /hasTimestamp/u);
  assert.match(runner, /NotSigned/u);
  assert.match(runner, /LOCALAPPDATA/u);
  assert.match(runner, /HKEY_CURRENT_USER/u);
  assert.match(runner, /parse_windows_registry_path/u);
  assert.match(
    runner,
    /OpenKey\(\s*parent,\s*winreg\.EnumKey\(parent,\s*index\),\s*0,\s*winreg\.KEY_READ\s*\|\s*view/su,
  );
  assert.match(runner, /DisplayName/u);
  assert.match(runner, /DisplayVersion/u);
  assert.match(runner, /InstallLocation/u);
  assert.match(runner, /UninstallString/u);
  assert.match(runner, /["']\/S["']/u);
  assert.match(runner, /uninstall\.exe/u);
  assert.match(runner, /optional-decisions/u);
  assert.match(runner, /optional-install/u);
  assert.match(runner, /forced-first/u);
  assert.match(runner, /forced-reopen/u);
  assert.match(runner, /installer-failure/u);
  assert.match(runner, /wait_for_binary_hash/u);
  assert.match(runner, /wait_for_update_installer_exit/u);
  assert.match(runner, /BUNDLE_TYPE_VAR_UNK/u);
  assert.match(runner, /BUNDLE_TYPE_VAR_NSS/u);
  assert.match(runner, /expected_nsis_binary_sha256/u);
  assert.match(runner, /file_version/u);
  assert.match(runner, /runtime_environment\.pop\(name, None\)/u);
  assert.match(runner, /"AUTOMATION_TOOL_UPDATE_INSTALL_PROBE"/u);
  assert.doesNotMatch(
    runner,
    /environment\["AUTOMATION_TOOL_UPDATE_INSTALL_PROBE"\]\s*=/u,
  );
  assert.match(runner, /\["0\.2\.0", "0\.3\.0", "0\.2\.0", "0\.4\.0"\]/u);

  assert.match(spec, /clickEnabledButton\("稍后提醒"\)/u);
  assert.match(spec, /clickEnabledButton\("跳过此版本"\)/u);
  assert.match(spec, /clickEnabledButton\("立即安装"\)/u);
  assert.match(spec, /ECONNREFUSED/u);
  assert.match(spec, /installation_failed/u);
  assert.match(wdio, /H822_WINDOWS_APP_BINARY/u);
  assert.match(gitignore, /frontend\/dist-h822-mac\//u);
  assert.match(gitignore, /frontend\/dist-h822-windows\//u);
});
