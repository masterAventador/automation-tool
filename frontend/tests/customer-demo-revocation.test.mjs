import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function read(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("C10-11 freezes account-wide and one-device revocation isolation", async () => {
  const contract = JSON.parse(
    await read("contracts/security/customer-demo-revocation-v1.json"),
  );

  assert.equal(contract.version, "customer-demo-revocation.v1");
  assert.deepEqual(contract.accountDisable.invalidates, [
    "account_access_sessions",
    "account_refresh_sessions",
    "owned_installations",
    "owned_device_credentials",
    "owned_device_sessions",
  ]);
  assert.deepEqual(contract.accountDisable.preserves, [
    "foreign_accounts",
    "foreign_installations",
    "foreign_sessions",
  ]);
  assert.deepEqual(contract.singleDeviceRevoke.invalidates, [
    "target_installation",
    "target_device_credential",
    "target_device_sessions",
  ]);
  assert.deepEqual(contract.singleDeviceRevoke.preserves, [
    "same_account_sibling_installations",
    "foreign_installations",
    "foreign_sessions",
  ]);
  assert.deepEqual(contract.anonymousMutationAllowlist, ["loginAccountSession"]);
  assert.equal(contract.anonymousBusinessWrites, 0);
});

test("C10-11 has one executable revocation matrix and OpenAPI write audit", async () => {
  const [runner, surfaceTest, emergencyTest, packageSource] = await Promise.all([
    read("scripts/run_c10_11_acceptance.py"),
    read("backend/tests/contract/test_customer_demo_revocation_surface.py"),
    read("backend/tests/integration/test_demo_account_emergency_operations.py"),
    read("frontend/package.json"),
  ]);

  for (const marker of [
    "test_emergency_revoke_is_atomic_scoped_audited_and_single_winner",
    "test_current_account_lists_and_revokes_only_one_owned_device",
    "test_revoked_installation_rejects_exchange_and_existing_session",
    "test_customer_demo_has_no_anonymous_business_write_operation",
  ]) {
    assert.ok(runner.includes(marker), `${marker} is missing`);
  }
  assert.match(surfaceTest, /anonymous_mutation_allowlist/u);
  assert.match(surfaceTest, /loginAccountSession/u);
  assert.match(emergencyTest, /foreign_session/u);
  assert.match(emergencyTest, /AccountSessionRejected/u);
  assert.match(packageSource, /test:c10-11-revocation/u);
});
