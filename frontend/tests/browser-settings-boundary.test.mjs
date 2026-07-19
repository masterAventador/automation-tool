import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("B5-04 exposes browser enums without executable paths", async () => {
  const [settings, common, adapter, native, component, tauriConfig, spec, runner, packageJson] = await Promise.all([
    readRepositoryFile("frontend/src-tauri/src/browser_settings.rs"),
    readRepositoryFile("frontend/src-tauri/src/browser_discovery.rs"),
    readRepositoryFile("frontend/src/platform/tauri/platform-adapter.ts"),
    readRepositoryFile("frontend/src-tauri/src/lib.rs"),
    readRepositoryFile("frontend/src/features/settings/BrowserSettings.tsx"),
    readRepositoryFile("frontend/src-tauri/tauri.browser-settings-e2e.conf.json"),
    readRepositoryFile("frontend/e2e-tauri/browser-settings.spec.ts"),
    readRepositoryFile("scripts/run_b5_04_acceptance.py"),
    readRepositoryFile("frontend/package.json"),
  ]);

  assert.match(settings, /BrowserSettingsService/u);
  assert.match(settings, /browser-selection-v1/u);
  assert.match(common, /GoogleChrome/u);
  assert.match(common, /MicrosoftEdge/u);
  assert.match(adapter, /invoke<unknown>\("get_browser_settings"\)/u);
  assert.match(adapter, /invoke<unknown>\("select_browser", \{ browser \}\)/u);
  assert.match(native, /fn get_browser_settings/u);
  assert.match(native, /fn select_browser\(\s*browser: browser_discovery::SupportedBrowser/u);
  assert.match(component, /保存浏览器选择/u);

  for (const source of [settings, adapter, native, component]) {
    assert.doesNotMatch(source, /executablePath|executable_path|applicationPath|application_path/u);
  }
  assert.doesNotMatch(adapter, /selectBrowser\([^)]*path/u);
  assert.doesNotMatch(component, /type=["']file["']|type=["']text["']/u);

  assert.match(tauriConfig, /com\.aventador\.automationtool\.b504acceptance/u);
  assert.match(tauriConfig, /"visible": false/u);
  assert.match(spec, /保存浏览器选择/u);
  assert.match(spec, /browser\.refresh\(\)/u);
  assert.match(runner, /TAURI_WEBDRIVER_PORT/u);
  assert.match(runner, /require_port_closed/u);
  assert.match(runner, /browser-selection-v1/u);
  assert.match(runner, /"pnpm\.cmd" if sys\.platform == "win32" else "pnpm"/u);
  assert.match(runner, /\[pnpm_executable\(\), "build:tauri:browser-settings-test"\]/u);
  assert.match(runner, /\[pnpm_executable\(\), "exec", "wdio"/u);
  assert.match(packageJson, /test:browser-settings-tauri/u);
});
