import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("D6-08 evaluates only stable candidate keys with a fixed local policy", async () => {
  const [policy, domain, candidate, extraction, wire, native] = await Promise.all([
    readRepositoryFile(
      "backend/src/automation_tool/control_plane/domain/douyin_candidate_policy.py",
    ),
    readRepositoryFile(
      "backend/src/automation_tool/control_plane/domain/__init__.py",
    ),
    readRepositoryFile("backend/src/automation_tool/protocol/douyin_candidate.py"),
    readRepositoryFile(
      "backend/src/automation_tool/executor/rpa/douyin/candidate_extraction.py",
    ),
    readRepositoryFile("contracts/protocol/executor-v1.schema.json"),
    readRepositoryFile("frontend/src-tauri/src/lib.rs"),
  ]);

  assert.match(policy, /douyin\.candidate-policy\.v1/u);
  assert.match(policy, /timedelta\(days=30\)/u);
  assert.match(policy, /DUPLICATE_IN_TASK = "duplicate_in_task"/u);
  assert.match(policy, /DUPLICATE_IN_HISTORY = "duplicate_in_history"/u);
  assert.match(policy, /BLACKLISTED = "blacklisted"/u);
  assert.match(policy, /\.dedupe_key/u);
  assert.match(domain, /DouyinCandidateDisposition/u);
  assert.doesNotMatch(policy, /platform_target_id|display_name|public_handle/u);
  assert.doesNotMatch(
    policy,
    /playwright|browser|selector|sqlalchemy|repository|database|httpx|requests\.|websocket|tauri/iu,
  );
  assert.doesNotMatch(extraction, /DouyinCandidateDisposition|candidate_policy/u);
  assert.doesNotMatch(candidate, /timedelta\(days=30\)|BLACKLISTED/u);
  assert.doesNotMatch(wire, /duplicate_in_task|duplicate_in_history|blacklisted/u);
  assert.doesNotMatch(native, /tauri::command[\s\S]{0,300}DouyinCandidateDisposition/u);
});
