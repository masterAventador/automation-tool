import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("B5-12 reports one typed non-sensitive Session fact through the production socket", async () => {
  const [
    protocol,
    reporter,
    ledger,
    service,
    repository,
    migration,
    websocket,
    acceptance,
  ] = await Promise.all([
    readRepositoryFile("contracts/protocol/executor-v1.schema.json"),
    readRepositoryFile("backend/src/automation_tool/executor/rpa/douyin/health.py"),
    readRepositoryFile("backend/src/automation_tool/executor/ledger.py"),
    readRepositoryFile(
      "backend/src/automation_tool/control_plane/application/platform_session_health.py",
    ),
    readRepositoryFile(
      "backend/src/automation_tool/control_plane/infrastructure/database/platform_session_health_repository.py",
    ),
    readRepositoryFile(
      "backend/migrations/versions/20260718_0014_platform_session_health.py",
    ),
    readRepositoryFile(
      "backend/src/automation_tool/control_plane/api/executor_websocket.py",
    ),
    readRepositoryFile("scripts/run_b5_12_acceptance.py"),
  ]);

  assert.match(protocol, /platform\.session_health/u);
  assert.match(reporter, /DouyinSessionDetector/u);
  assert.match(reporter, /record_platform_session/u);
  assert.match(ledger, /executor_platform_sessions/u);
  assert.match(service, /session_revision/u);
  assert.match(repository, /projection\.circuit_open and not pending\.circuit_open/u);
  assert.match(websocket, /PlatformSessionHealthEnvelope/u);
  assert.match(acceptance, /connect_executor_websocket/u);
  assert.match(acceptance, /sync_playwright/u);

  const migrationColumns = [
    ...migration.matchAll(/sa\.Column\("([a-z_]+)"/gu),
  ].map((match) => match[1]);
  assert.deepEqual(migrationColumns, [
    "installation_id",
    "platform",
    "state",
    "session_revision",
    "observed_at",
    "updated_at",
  ]);
  for (const forbidden of [
    "cookie",
    "profile_id",
    "profile_path",
    "qr_code",
    "captcha",
    "page_text",
  ]) {
    assert.doesNotMatch(migration, new RegExp(forbidden, "u"));
  }
});
