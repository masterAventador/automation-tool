import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("D6-02 keeps Douyin search-page discovery read-only and versioned", async () => {
  const [searchPage, pageVersion, protocol, native] = await Promise.all([
    readRepositoryFile("backend/src/automation_tool/executor/rpa/douyin/search_page.py"),
    readRepositoryFile("backend/src/automation_tool/executor/rpa/douyin/page_version.py"),
    readRepositoryFile("contracts/protocol/executor-v1.schema.json"),
    readRepositoryFile("frontend/src-tauri/src/lib.rs"),
  ]);

  assert.match(searchPage, /douyin\.search-page\.v1/u);
  assert.match(searchPage, /DouyinPageVersionModel/u);
  assert.match(searchPage, /DouyinSearchPageState/u);
  assert.match(searchPage, /CONFLICTING_ANCHORS/u);
  assert.match(searchPage, /PAGE_UNAVAILABLE/u);
  assert.match(pageVersion, /douyin\.web\.v1/u);
  assert.doesNotMatch(searchPage, /https:\/\/www\.douyin\.com/u);
  assert.doesNotMatch(
    searchPage,
    /\.(?:click|dblclick|fill|type|press|uncheck|set_checked|select_option|hover|drag_to|evaluate|goto|reload)\s*\(/u,
  );
  assert.doesNotMatch(
    searchPage,
    /context\.cookies|document\.cookie|storage_state|localStorage|sessionStorage/iu,
  );
  assert.doesNotMatch(searchPage, /control_plane|httpx|requests\./iu);
  assert.doesNotMatch(protocol, /selector|playwright_page|profile_directory/iu);
  assert.doesNotMatch(native, /tauri::command[\s\S]{0,300}(selector|playwright_page)/u);
});
