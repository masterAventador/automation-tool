import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("D6-07 keeps raw result facts inside the versioned Page Object", async () => {
  const [extraction, pageObject, candidate, wire, native] = await Promise.all([
    readRepositoryFile(
      "backend/src/automation_tool/executor/rpa/douyin/candidate_extraction.py",
    ),
    readRepositoryFile(
      "backend/src/automation_tool/executor/rpa/douyin/search_page.py",
    ),
    readRepositoryFile("backend/src/automation_tool/protocol/douyin_candidate.py"),
    readRepositoryFile("contracts/protocol/executor-v1.schema.json"),
    readRepositoryFile("frontend/src-tauri/src/lib.rs"),
  ]);

  assert.match(extraction, /douyin\.candidate-extraction\.v1/u);
  assert.match(extraction, /DouyinCandidate/u);
  assert.match(extraction, /candidate_items/u);
  assert.match(pageObject, /_CANDIDATE_AUTHOR_SELECTORS/u);
  assert.match(pageObject, /_CANDIDATE_NAME_SELECTORS/u);
  assert.match(pageObject, /data-user-id/u);
  assert.match(pageObject, /data-user-handle/u);
  assert.match(pageObject, /def candidate_items/u);
  assert.doesNotMatch(extraction, /(?:aria-label|data-e2e|role=|placeholder=)/u);
  assert.doesNotMatch(extraction, /\.locator\s*\(/u);
  assert.doesNotMatch(
    extraction,
    /(?:\.evaluate\s*\(|inner_html|\.content\s*\(|context\.cookies|document\.cookie|storage_state|localStorage|sessionStorage)/iu,
  );
  assert.doesNotMatch(extraction, /control_plane|httpx|requests\.|websocket/iu);
  assert.doesNotMatch(
    pageObject,
    /(?:inner_html|\.content\s*\(|context\.cookies|document\.cookie|storage_state|localStorage|sessionStorage)/iu,
  );
  assert.doesNotMatch(candidate, /(?:avatar|biography|contact|absolute_url|raw_html)/iu);
  assert.doesNotMatch(wire, /candidate|platform_target_id|public_handle|page_revision/iu);
  assert.doesNotMatch(native, /tauri::command[\s\S]{0,300}(candidate|platform_target_id)/iu);
});
