import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("D6-09 persists bounded target previews without widening the App or wire", async () => {
  const [migration, schema, repository, records, wire, native] = await Promise.all([
    readRepositoryFile(
      "backend/migrations/versions/20260718_0016_task_targets.py",
    ),
    readRepositoryFile(
      "backend/src/automation_tool/control_plane/infrastructure/database/schema.py",
    ),
    readRepositoryFile(
      "backend/src/automation_tool/control_plane/infrastructure/database/task_target_repository.py",
    ),
    readRepositoryFile(
      "backend/src/automation_tool/control_plane/application/task_targets.py",
    ),
    readRepositoryFile("contracts/protocol/executor-v1.schema.json"),
    readRepositoryFile("frontend/src-tauri/src/lib.rs"),
  ]);

  assert.match(migration, /20260718_0016/u);
  assert.match(migration, /"task_targets"/u);
  assert.match(migration, /\["task_id", "installation_id"\]/u);
  assert.match(migration, /uq_task_targets_task_ordinal/u);
  assert.match(schema, /ix_task_targets_installation_task_page/u);
  assert.match(schema, /ix_task_targets_installation_history/u);
  assert.match(repository, /evaluate_douyin_candidates/u);
  assert.match(repository, /\.with_for_update\(\)/u);
  assert.match(repository, /task_targets\.c\.installation_id/u);
  assert.match(records, /TaskTargetRecord/u);
  assert.doesNotMatch(
    migration,
    /avatar|biography|phone|email|contact|page_body|raw_html|absolute_url|profile_url/iu,
  );
  assert.doesNotMatch(repository, /playwright|browser|selector|cookie|storage/iu);
  assert.doesNotMatch(wire, /task_targets|duplicate_in_task|duplicate_in_history|blacklisted/u);
  assert.doesNotMatch(native, /tauri::command[\s\S]{0,300}TaskTargetRecord/u);
});
