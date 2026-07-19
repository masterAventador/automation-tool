import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("B5-09 derives a closed Douyin session state only from bounded page evidence", async () => {
  const [session, runtime, protocol, native] = await Promise.all([
    readRepositoryFile("backend/src/automation_tool/executor/rpa/douyin/session.py"),
    readRepositoryFile("backend/src/automation_tool/executor/browser_runtime.py"),
    readRepositoryFile("contracts/protocol/executor-v1.schema.json"),
    readRepositoryFile("frontend/src-tauri/src/lib.rs"),
  ]);

  for (const state of ["healthy", "expired", "missing", "risk", "unknown"]) {
    assert.match(session, new RegExp(`\\b${state.toUpperCase()}\\b`, "u"));
  }
  assert.match(session, /douyin\.session\.v1/u);
  assert.match(session, /https:\/\/www\.douyin\.com\/user\/self/u);
  assert.match(session, /rmc\.bytedance\.com\/verifycenter\/captcha/u);
  assert.match(session, /circuit_open/u);
  assert.match(runtime, /BrowserWindow/u);
  assert.doesNotMatch(session, /context\.cookies|document\.cookie|storage_state/u);
  assert.doesNotMatch(protocol, /cookie|profile_directory|playwright_page/iu);
  assert.doesNotMatch(native, /tauri::command[\s\S]{0,300}playwright_page/u);
});
