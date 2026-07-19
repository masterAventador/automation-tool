import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("B5-10 opens a dedicated external window and rechecks only real page facts", async () => {
  const [login, session, runtime, protocol, native] = await Promise.all([
    readRepositoryFile("backend/src/automation_tool/executor/rpa/douyin/login.py"),
    readRepositoryFile("backend/src/automation_tool/executor/rpa/douyin/session.py"),
    readRepositoryFile("backend/src/automation_tool/executor/browser_runtime.py"),
    readRepositoryFile("contracts/protocol/executor-v1.schema.json"),
    readRepositoryFile("frontend/src-tauri/src/lib.rs"),
  ]);

  for (const state of [
    "login_required",
    "awaiting_scan",
    "awaiting_confirmation",
    "qr_expired",
    "healthy",
    "risk",
    "unknown",
  ]) {
    assert.match(login, new RegExp(`\\b${state.toUpperCase()}\\b`, "u"));
  }
  assert.match(login, /douyin\.qr-login\.v1/u);
  assert.match(login, /DOUYIN_SESSION_PROBE_URL/u);
  assert.match(login, /\.open_window\(\)/u);
  assert.match(login, /DouyinSessionDetector/u);
  assert.match(login, /def recheck\(self\)/u);
  assert.match(session, /https:\/\/www\.douyin\.com\/user\/self/u);
  assert.match(protocol, /session\.login_required/u);
  assert.match(runtime, /headless=False/u);
  assert.doesNotMatch(login, /LoginSignal|QrScanned|Authenticated|qr_scanned\s*[:=]|authenticated: bool/u);
  assert.doesNotMatch(login, /context\.cookies|document\.cookie|storage_state/u);
  assert.doesNotMatch(native, /tauri::command[\s\S]{0,300}(playwright_page|profile_directory)/u);
});
