import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function read(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("C10-13 freezes safe deployment rollback and emergency invariants", async () => {
  const contract = JSON.parse(
    await read("contracts/operations/customer-demo-runbook-v1.json"),
  );

  assert.equal(contract.version, "customer-demo-operations-runbook.v1");
  assert.deepEqual(contract.procedures, [
    "preflight",
    "backup",
    "migration_and_deploy",
    "health_verification",
    "account_and_device_revocation",
    "isolated_restore",
    "application_rollback",
    "emergency_stop",
    "recovery_and_closeout",
  ]);
  assert.equal(contract.invariants.namedCloudTargetRequired, true);
  assert.equal(contract.invariants.maximumControlPlaneReplicas, 1);
  assert.equal(contract.invariants.databaseDowngrade, "forbidden");
  assert.equal(contract.invariants.restoreDestination, "new_isolated_database");
  assert.equal(contract.invariants.rollbackImage, "explicit_previous_digest");
  assert.equal(contract.invariants.emergencyStopPreservesDatabase, true);
  assert.deepEqual(contract.secretInputs, ["read_only_secret_file", "stdin"]);
});

test("C10-13 runbook is executable, evidence-bound, and avoids destructive shortcuts", async () => {
  const [runbook, runner, packageSource] = await Promise.all([
    read("docs/customer-demo-operations-runbook.md"),
    read("scripts/run_c10_13_acceptance.py"),
    read("frontend/package.json"),
  ]);

  for (const heading of [
    "## 1. 发布前检查",
    "## 2. 备份",
    "## 3. 迁移与部署",
    "## 4. 健康验证",
    "## 5. 账号与设备吊销",
    "## 6. 隔离恢复",
    "## 7. 应用回滚",
    "## 8. 紧急停服",
    "## 9. 恢复与收尾",
  ]) {
    assert.ok(runbook.includes(heading), `${heading} is missing`);
  }
  assert.match(runbook, /不得对数据库执行 downgrade/u);
  assert.match(runbook, /不得覆盖生产数据库/u);
  assert.match(runbook, /不得使用 `down --volumes`/u);
  assert.doesNotMatch(runbook, /--password\b|--capability\s+\S+/u);
  for (const marker of [
    "run_c10_03_acceptance.py",
    "run_c10_10_acceptance.py",
    "run_c10_11_acceptance.py",
    "--full-rehearsal",
    "--print-checklist",
  ]) {
    assert.ok(runner.includes(marker), `${marker} is missing`);
  }
  assert.match(packageSource, /test:c10-13-runbook/u);
});
