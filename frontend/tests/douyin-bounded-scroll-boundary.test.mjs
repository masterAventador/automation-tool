import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("D6-05 keeps discovery scrolling bounded, cancellable, and selector-free", async () => {
  const [scroll, pageObject, search, protocol, wire, native] = await Promise.all([
    readRepositoryFile(
      "backend/src/automation_tool/executor/rpa/douyin/bounded_scroll.py",
    ),
    readRepositoryFile(
      "backend/src/automation_tool/executor/rpa/douyin/search_page.py",
    ),
    readRepositoryFile("backend/src/automation_tool/executor/rpa/douyin/search.py"),
    readRepositoryFile("backend/src/automation_tool/protocol/douyin_search.py"),
    readRepositoryFile("contracts/protocol/executor-v1.schema.json"),
    readRepositoryFile("frontend/src-tauri/src/lib.rs"),
  ]);

  assert.match(scroll, /douyin\.bounded-scroll\.v1/u);
  assert.match(scroll, /MAX_SCROLL_ROUNDS = 20/u);
  assert.match(scroll, /DouyinSearchInput/u);
  assert.match(scroll, /DouyinSearchExecutionObservation/u);
  assert.match(scroll, /cancellation_requested/u);
  assert.match(scroll, /\.mouse\.wheel/u);
  assert.match(scroll, /result_item_count/u);
  assert.match(pageObject, /_RESULT_ITEM_SELECTORS/u);
  assert.match(pageObject, /def result_item_count/u);
  assert.doesNotMatch(scroll, /(?:aria-label|data-e2e|role=|placeholder=)/u);
  assert.doesNotMatch(scroll, /\.locator\s*\(/u);
  assert.doesNotMatch(scroll, /https:\/\/www\.douyin\.com/u);
  assert.doesNotMatch(
    scroll,
    /(?:\.evaluate\s*\(|context\.cookies|document\.cookie|storage_state|localStorage|sessionStorage)/iu,
  );
  assert.doesNotMatch(scroll, /(?:comment|private_message|direct_message|\.click\s*\()/iu);
  assert.doesNotMatch(scroll, /control_plane|httpx|requests\./iu);
  assert.doesNotMatch(search, /_RESULT_ITEM_SELECTORS|mouse\.wheel/u);
  assert.doesNotMatch(protocol, /playwright|selector|scroll/iu);
  assert.doesNotMatch(wire, /selector|profile_directory|scroll_delta/iu);
  assert.doesNotMatch(native, /tauri::command[\s\S]{0,300}(selector|playwright_page)/u);
});
