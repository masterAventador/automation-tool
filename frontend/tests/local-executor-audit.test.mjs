import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("E4-01 local executor audit locks every old source and deletion boundary", async () => {
  const structure = await readRepositoryFile("docs/project-structure.md");
  const start = structure.indexOf("### 10.2 `local_executor.rs` 逐项清单");
  const end = structure.indexOf("### 10.3 `sidecar_package.rs` 逐项清单");
  assert.ok(start >= 0 && end > start);
  const audit = structure.slice(start, end);

  assert.match(audit, /#### 10\.2\.1 来源文件覆盖表/u);
  for (const source of [
    "frontend/src-tauri/src/local_executor.rs",
    "frontend/src-tauri/tests/local_executor.rs",
    "frontend/src-tauri/src/main.rs",
    "frontend/src-tauri/src/lib.rs",
    "frontend/src/platform/tauri.ts",
    "frontend/src-tauri/src/social_operations_runtime.rs",
    "backend/src/agent_platform/capabilities/social_operations/local_executor_protocol.py",
    "backend/src/agent_platform/capabilities/social_operations/device_account_service.py",
    "contracts/capabilities/social-operations/local-executor-v1.schema.json",
  ]) {
    assert.match(audit, new RegExp(source.replaceAll("/", "\\/"), "u"));
  }

  for (const forbiddenBoundary of [
    "tenant_id",
    "approval_id",
    "audit_correlation_id",
    "Core Artifact",
    "serde_json::Value",
    "--social-operations-sidecar",
    "SocialOperationsRuntime",
  ]) {
    assert.match(audit, new RegExp(forbiddenBoundary, "u"));
  }
  for (const destination of ["E4-02", "E4-06", "E4-07", "E4-08", "E4-09", "E4-10"]) {
    assert.match(audit, new RegExp(destination, "u"));
  }

  const [cargo, backend, frontend] = await Promise.all([
    readRepositoryFile("frontend/src-tauri/Cargo.toml"),
    readRepositoryFile("backend/pyproject.toml"),
    readRepositoryFile("frontend/package.json"),
  ]);
  for (const manifest of [cargo, backend, frontend]) {
    assert.doesNotMatch(manifest, /agent-platform|social-operations-sidecar/u);
  }
});
