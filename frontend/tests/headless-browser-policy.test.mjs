import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);
const integrationRoot = new URL("backend/tests/integration/", repositoryRoot);
const tauriRoot = new URL("frontend/src-tauri/", repositoryRoot);
const explicitlyVisibleAcceptances = new Set([
  "test_douyin_qr_login_browser.py",
  "test_douyin_session_browser.py",
  "test_packaged_browser_probe.py",
]);
const mixedHeadlessAndVisibleAcceptances = new Map([
  ["test_douyin_qr_login_browser.py", { requests: 3, headless: 1 }],
  ["test_douyin_session_browser.py", { requests: 2, headless: 1 }],
]);

test("routine browser tests are globally headless and direct runtime callers cannot regress", async () => {
  const playwrightConfig = await readFile(
    new URL("frontend/playwright.config.ts", repositoryRoot),
    "utf8",
  );
  assert.match(playwrightConfig, /use:\s*\{[\s\S]*?headless:\s*true/u);

  const integrationFiles = (await readdir(integrationRoot)).filter(
    (name) => name.endsWith("_browser.py") && !explicitlyVisibleAcceptances.has(name),
  );
  for (const fileName of integrationFiles) {
    const source = await readFile(new URL(fileName, integrationRoot), "utf8");
    const calls = source.split("BrowserLaunchRequest(").slice(1);
    for (const call of calls) {
      const requestPrefix = call.split("\n        )", 1)[0];
      assert.match(requestPrefix, /headless=True/u, `${fileName} must stay headless`);
    }
  }

  for (const [fileName, expected] of mixedHeadlessAndVisibleAcceptances) {
    const source = await readFile(new URL(fileName, integrationRoot), "utf8");
    assert.equal(source.split("BrowserLaunchRequest(").length - 1, expected.requests);
    assert.equal(source.match(/headless=True/gu)?.length ?? 0, expected.headless);
    assert.match(source, /pytest\.mark\.skipif/u);
  }
});

test("every automated Tauri acceptance window is globally hidden", async () => {
  const configurationNames = (await readdir(tauriRoot)).filter(
    (name) => name === "tauri.test.conf.json" || /^tauri\..+-e2e\.conf\.json$/u.test(name),
  );
  assert.ok(configurationNames.length > 0);
  for (const configurationName of configurationNames) {
    const configuration = JSON.parse(
      await readFile(new URL(configurationName, tauriRoot), "utf8"),
    );
    const windows = configuration.app?.windows;
    assert.ok(Array.isArray(windows) && windows.length > 0, configurationName);
    assert.ok(
      windows.every((window) => window.visible === false),
      `${configurationName} must keep every automated App window hidden`,
    );
  }
});
