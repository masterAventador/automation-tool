import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("D6-06 candidate model stays stable, minimal, versioned, and not wired early", async () => {
  const [candidate, protocol, wire, scroll, native] = await Promise.all([
    readRepositoryFile("backend/src/automation_tool/protocol/douyin_candidate.py"),
    readRepositoryFile("backend/src/automation_tool/protocol/__init__.py"),
    readRepositoryFile("contracts/protocol/executor-v1.schema.json"),
    readRepositoryFile(
      "backend/src/automation_tool/executor/rpa/douyin/bounded_scroll.py",
    ),
    readRepositoryFile("frontend/src-tauri/src/lib.rs"),
  ]);

  assert.match(candidate, /DOUYIN_CANDIDATE_VERSION = "douyin\.candidate\.v1"/u);
  assert.match(candidate, /MAX_DOUYIN_TARGET_ID_CHARACTERS = 128/u);
  assert.match(candidate, /MAX_CANDIDATE_DISPLAY_NAME_CHARACTERS = 80/u);
  assert.match(candidate, /MAX_CANDIDATE_PUBLIC_HANDLE_CHARACTERS = 64/u);
  assert.match(candidate, /GENERAL_SEARCH_AUTHOR = "general_search_author"/u);
  assert.match(candidate, /sha256/u);
  assert.match(candidate, /automation-tool\.douyin\.candidate-key\.v1/u);
  assert.match(candidate, /MAX_CROSS_RUNTIME_SEQUENCE/u);
  assert.match(protocol, /from automation_tool\.protocol\.douyin_candidate import/u);
  assert.match(protocol, /"DouyinCandidate"/u);
  assert.doesNotMatch(
    candidate,
    /(?:avatar|biography|\bbio\b|phone|email|contact|page_body|raw_html|absolute_url|profile_url)/iu,
  );
  assert.doesNotMatch(candidate, /playwright|control_plane|sqlalchemy|database/iu);
  assert.doesNotMatch(scroll, /DouyinCandidate|display_name|public_handle|platform_target_id/u);
  assert.doesNotMatch(wire, /DouyinCandidate|dedupe_key|platform_target_id/u);
  assert.doesNotMatch(native, /tauri::command[\s\S]{0,300}DouyinCandidate/u);
});
