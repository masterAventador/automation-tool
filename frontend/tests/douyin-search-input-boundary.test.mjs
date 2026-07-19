import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("D6-03 keeps one Python search-input policy and matching desktop bounds", async () => {
  const [policy, definition, api, gateway, form, native, openapi, acceptance] =
    await Promise.all([
      readRepositoryFile("backend/src/automation_tool/protocol/douyin_search.py"),
      readRepositoryFile(
        "backend/src/automation_tool/control_plane/domain/task_definitions.py",
      ),
      readRepositoryFile("backend/src/automation_tool/control_plane/api/tasks.py"),
      readRepositoryFile(
        "frontend/src/features/task-create/task-creation-gateway.ts",
      ),
      readRepositoryFile("frontend/src/features/task-create/TaskCreate.tsx"),
      readRepositoryFile("frontend/src-tauri/src/control_plane.rs"),
      readRepositoryFile("contracts/openapi/control-plane.v1.json"),
      readRepositoryFile("frontend/e2e-tauri/task-create-form.spec.ts"),
    ]);

  const schema = JSON.parse(openapi).components.schemas.TaskCreateRequest.properties;
  assert.match(policy, /DOUYIN_SEARCH_INPUT_VERSION = "douyin\.search-input\.v1"/u);
  assert.match(policy, /MAX_SEARCH_KEYWORD_CHARACTERS = 80/u);
  assert.match(policy, /MAX_TASK_TARGET_LIMIT = 100/u);
  assert.match(definition, /from automation_tool\.protocol import/u);
  assert.match(definition, /DouyinSearchInput\(/u);
  assert.doesNotMatch(definition, /MAX_SEARCH_KEYWORD_CHARACTERS\s*=\s*80/u);
  assert.doesNotMatch(definition, /MAX_TASK_TARGET_LIMIT\s*=\s*100/u);
  assert.match(api, /MAX_SEARCH_KEYWORD_CHARACTERS/u);
  assert.match(api, /MAX_TASK_TARGET_LIMIT/u);
  assert.match(gateway, /export const MAX_SEARCH_KEYWORD_CHARACTERS = 80/u);
  assert.match(gateway, /export const MAX_TASK_TARGET_LIMIT = 100/u);
  assert.match(gateway, /Array\.from\(value\)\.length/u);
  assert.match(form, /douyinSearchKeywordSchema/u);
  assert.match(form, /MAX_TASK_TARGET_LIMIT/u);
  assert.match(native, /const MAX_SEARCH_KEYWORD_CHARACTERS: usize = 80/u);
  assert.match(native, /const MAX_TASK_TARGET_LIMIT: u16 = 100/u);
  assert.match(acceptance, /"control\\u0085character"/u);
  assert.match(acceptance, /"😀"\.repeat\(80\)/u);
  assert.match(acceptance, /setValue\("100"\)/u);
  assert.equal(schema.searchKeyword.maxLength, 80);
  assert.equal(schema.targetLimit.maximum, 100);
});
