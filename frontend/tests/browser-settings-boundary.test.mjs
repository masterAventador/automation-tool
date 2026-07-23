import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("B5-04 keeps its historical path-free core while EB-10 removes every production entry", async () => {
  const [settings, common, adapter, native, app, evidence] = await Promise.all([
    readRepositoryFile("frontend/src-tauri/src/browser_settings.rs"),
    readRepositoryFile("frontend/src-tauri/src/browser_discovery.rs"),
    readRepositoryFile("frontend/src/platform/tauri/platform-adapter.ts"),
    readRepositoryFile("frontend/src-tauri/src/lib.rs"),
    readRepositoryFile("frontend/src/app/App.tsx"),
    readRepositoryFile("docs/development/EB-10.md"),
  ]);

  assert.match(settings, /BrowserSettingsService/u);
  assert.match(settings, /browser-selection-v1/u);
  assert.match(common, /GoogleChrome/u);
  assert.match(common, /MicrosoftEdge/u);
  assert.match(settings, /pub struct BrowserSettingsSnapshot[\s\S]*available_browsers[\s\S]*selected_browser/u);
  assert.doesNotMatch(settings, /pub struct BrowserSettingsSnapshot[\s\S]{0,240}executable_path/u);
  assert.match(native, /pub mod browser_settings;/u);
  for (const source of [adapter, native, app]) {
    assert.doesNotMatch(source, /get_browser_settings|select_browser|BrowserSettingsSnapshot/u);
  }
  assert.doesNotMatch(adapter, /getBrowserSettings|selectBrowser/u);
  assert.doesNotMatch(app, /BrowserSettings|保存浏览器选择/u);
  assert.match(evidence, /不存在[\s\S]{0,40}可达的用户入口或生产消费者/u);
});
