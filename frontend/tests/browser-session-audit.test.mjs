import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("B5-01 browser session audit locks sources, migration boundaries, and destinations", async () => {
  const structure = await readRepositoryFile("docs/project-structure.md");
  const start = structure.indexOf("### 10.4 `browser_session.rs` 逐项清单");
  const end = structure.indexOf("### 10.5 跨模块保留与明确排除");
  assert.ok(start >= 0 && end > start);
  const audit = structure.slice(start, end);

  for (const heading of [
    "#### 10.4.1 B5-01 来源文件与测试证据",
    "#### 10.4.2 当前 Profile 与私有目录契约",
    "#### 10.4.3 当前 Session 状态契约",
    "#### 10.4.4 安全注销时序",
    "#### 10.4.5 强制删除的账号、RBAC 与 Cookie 边界",
  ]) {
    assert.match(audit, new RegExp(heading.replaceAll(".", "\\."), "u"));
  }

  for (const source of [
    "frontend/src-tauri/src/browser_session.rs",
    "frontend/src-tauri/tests/browser_session.rs",
    "frontend/src-tauri/src/social_operations_runtime.rs",
    "backend/src/agent_platform/capabilities/social_operations/device_account_service.py",
    "contracts/capabilities/social-operations/device-account-v1.md",
  ]) {
    assert.match(audit, new RegExp(source.replaceAll("/", "\\/"), "u"));
  }

  for (const preservedBoundary of [
    "app_data_dir",
    "profile_id",
    "canonical UUIDv4",
    "0700",
    "symlink",
    "reparse point",
    "session_revision",
    "circuit_open",
    "OUTCOME_UNCERTAIN",
  ]) {
    assert.match(audit, new RegExp(preservedBoundary, "u"));
  }

  for (const forbiddenBoundary of [
    "HashMap",
    "active_account",
    "EncryptedCookieVault",
    ".cookie-key",
    "SOC1",
    "tenant_id",
    "owner_user_id",
    "RBAC",
    "Entitlement",
    "SocialOperationsRuntime",
  ]) {
    assert.match(audit, new RegExp(forbiddenBoundary.replaceAll(".", "\\."), "u"));
  }

  for (const destination of [
    "B5-02",
    "B5-03",
    "B5-05",
    "B5-06",
    "B5-07",
    "B5-08",
    "B5-09",
    "B5-10",
    "B5-11",
    "B5-12",
    "B5-14",
  ]) {
    assert.match(audit, new RegExp(destination, "u"));
  }

  for (const manifestPath of [
    "frontend/src-tauri/Cargo.toml",
    "backend/pyproject.toml",
    "frontend/package.json",
  ]) {
    const manifest = await readRepositoryFile(manifestPath);
    assert.doesNotMatch(
      manifest,
      /agent-platform|chacha20poly1305|social-operations|device-account-v1/u,
    );
  }
});
