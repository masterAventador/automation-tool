import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("D6-04 keeps search execution bounded, single-shot, and Executor-local", async () => {
  const [execution, pageObject, pageVersion, protocol, wire, native] =
    await Promise.all([
      readRepositoryFile("backend/src/automation_tool/executor/rpa/douyin/search.py"),
      readRepositoryFile(
        "backend/src/automation_tool/executor/rpa/douyin/search_page.py",
      ),
      readRepositoryFile(
        "backend/src/automation_tool/executor/rpa/douyin/page_version.py",
      ),
      readRepositoryFile("backend/src/automation_tool/protocol/douyin_search.py"),
      readRepositoryFile("contracts/protocol/executor-v1.schema.json"),
      readRepositoryFile("frontend/src-tauri/src/lib.rs"),
    ]);

  assert.match(execution, /douyin\.search-execution\.v1/u);
  assert.match(execution, /DouyinSearchInput/u);
  assert.match(execution, /DouyinSearchPage/u);
  assert.match(execution, /DOUYIN_HOME_URL/u);
  assert.match(execution, /douyin_search_results_url/u);
  assert.match(execution, /no_wait_after=True/u);
  assert.match(execution, /wait_for_url/u);
  assert.match(execution, /_executed/u);
  assert.match(pageObject, /wait_for_home_ready/u);
  assert.match(pageObject, /wait_for_results_ready/u);
  assert.match(pageVersion, /def douyin_search_results_url/u);
  assert.doesNotMatch(execution, /https:\/\/www\.douyin\.com/u);
  assert.doesNotMatch(execution, /(?:aria-label|data-e2e|role=|placeholder=)/u);
  assert.doesNotMatch(execution, /\.locator\s*\(/u);
  assert.doesNotMatch(
    execution,
    /(?:\.evaluate\s*\(|context\.cookies|document\.cookie|storage_state|localStorage|sessionStorage)/iu,
  );
  assert.doesNotMatch(execution, /(?:scroll|comment|message|private_message)/iu);
  assert.doesNotMatch(execution, /control_plane|httpx|requests\./iu);
  assert.doesNotMatch(protocol, /playwright|selector|douyin\.com/iu);
  assert.doesNotMatch(wire, /keyword.*playwright|selector|profile_directory/iu);
  assert.doesNotMatch(native, /tauri::command[\s\S]{0,300}(selector|playwright_page)/u);
});
