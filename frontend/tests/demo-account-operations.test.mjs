import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function read(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("C10-06 keeps Demo account operations on one non-HTTP capability boundary", async () => {
  const [cli, app, job, role] = await Promise.all([
    read("backend/src/automation_tool/control_plane/bootstrap/account_operations_cli.py"),
    read("backend/src/automation_tool/control_plane/bootstrap/app.py"),
    read("deploy/operations/account-operations-job.v1.json"),
    read("deploy/operations/role.sql"),
  ]);

  for (const command of ["create", "disable", "restore", "reset", "emergency-revoke"]) {
    assert.ok(cli.includes(command), `${command} is missing`);
  }
  assert.match(cli, /input_stream/u);
  assert.match(cli, /expected-revision/u);
  assert.doesNotMatch(app, /account.operations|operations\/accounts|admin\/accounts/iu);
  const contract = JSON.parse(job);
  assert.deepEqual(contract.commands, [
    "create",
    "disable",
    "restore",
    "reset",
    "emergency-revoke",
  ]);
  assert.equal(contract.publicNetworkAccess, false);
  assert.equal(contract.secretDelivery, "fixed_read_only_files");
  assert.equal(contract.passwordAndCapabilityInput, "bounded_json_stdin");
  assert.equal(contract.recoveryTokenOutput, "single_use_stdout_no_logging");
  assert.equal(contract.databaseRole, "automation_tool_operations");
  assert.match(role, /CREATE ROLE automation_tool_operations/u);
  assert.match(role, /NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS/u);
  assert.match(role, /users,[\s\S]*account_session_families,[\s\S]*installations,[\s\S]*device_sessions/u);
  assert.doesNotMatch(
    role,
    /GRANT ALL|GRANT CREATE|\btasks,|\bPASSWORD\b|pg_(?:read|write)_all_data/ui,
  );
});

test("C10-06 emergency revocation is one account-scoped database transaction", async () => {
  const [repository, acceptance] = await Promise.all([
    read("backend/src/automation_tool/control_plane/infrastructure/database/customer_account_repository.py"),
    read("scripts/run_c10_06_acceptance.py"),
  ]);

  for (const marker of [
    "account_session_families",
    "account_session_tokens",
    "installations",
    "device_credentials",
    "device_sessions",
    "with_for_update",
    "operations_emergency_revoked",
  ]) {
    assert.ok(repository.includes(marker), `${marker} is missing`);
  }
  for (const marker of [
    "emergency-revoke",
    "revokedDeviceCount",
    "account.disabled",
    "session.all_revoked",
    "device.revoked",
    "AUTOMATION_TOOL_RUNTIME_SECRET_MODE=files",
    "PortBindings",
  ]) {
    assert.ok(acceptance.includes(marker), `${marker} is missing`);
  }
  assert.doesNotMatch(acceptance, /--publish|-p["']/u);
});
