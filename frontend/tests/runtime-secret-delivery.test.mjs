import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function read(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("C10-05 inventories only the fixed server-owned runtime secrets", async () => {
  const inventory = JSON.parse(await read("deploy/secrets/inventory.v1.json"));

  assert.equal(inventory.version, "customer-demo-secrets.v1");
  assert.equal(inventory.delivery.directory, "/run/secrets");
  assert.equal(inventory.delivery.productionMode, "files");
  assert.equal(inventory.delivery.maximumBytesPerSecret, 8192);
  assert.deepEqual(
    inventory.secrets.map(({ id, fileName }) => [id, fileName]),
    [
      ["database_url", "database-url"],
      ["account_password_pepper", "account-password-pepper"],
      ["account_fingerprint_key", "account-fingerprint-key"],
      ["account_operations_capability_digest", "account-operations-capability-digest"],
      ["action_authorization_private_key", "action-authorization-private-key"],
    ],
  );
  assert.ok(inventory.secrets.every(({ forbidden }) =>
    ["git", "image", "environment", "argv", "logs", "webview"].every((value) =>
      forbidden.includes(value)
    )
  ));
  assert.equal(inventory.nonSecrets.accountSessionSigningSecret, "not_used_opaque_tokens");
  assert.equal(inventory.nonSecrets.deviceCredentialSigningKey, "not_used_device_owns_key");
});

test("C10-05 production loader owns a bounded no-follow fixed-file boundary", async () => {
  const [loader, dockerfile, database, accounts, actions, operations] = await Promise.all([
    read("backend/src/automation_tool/control_plane/bootstrap/runtime_secrets.py"),
    read("backend/Dockerfile"),
    read("backend/src/automation_tool/control_plane/bootstrap/database.py"),
    read("backend/src/automation_tool/control_plane/bootstrap/account_sessions.py"),
    read("backend/src/automation_tool/control_plane/bootstrap/action_execution.py"),
    read("backend/src/automation_tool/control_plane/bootstrap/account_operations_cli.py"),
  ]);

  assert.match(dockerfile, /AUTOMATION_TOOL_RUNTIME_SECRET_MODE=files/u);
  assert.match(loader, /\/run\/secrets/u);
  assert.match(loader, /O_NOFOLLOW/u);
  assert.match(loader, /fstat/u);
  assert.match(loader, /S_ISREG/u);
  assert.match(loader, /8192/u);
  assert.match(loader, /st_uid/u);
  assert.match(loader, /st_mode/u);
  assert.doesNotMatch(loader, /Path\(.*environment|glob|resolve\(/u);
  for (const source of [database, accounts, actions, operations]) {
    assert.match(source, /runtime_secret/u);
  }
});

test("C10-05 proves file-only delivery and restart rotation in a real private stack", async () => {
  const acceptance = await read("scripts/run_c10_05_acceptance.py");

  for (const marker of [
    "POSTGRES_PASSWORD_FILE",
    "/proc/1/environ",
    "ReadonlyRootfs",
    "PortBindings",
    "account-password-pepper",
    "action-authorization-private-key",
    "rotation_requires_restart",
    "no-new-privileges=true",
  ]) {
    assert.ok(acceptance.includes(marker), `${marker} is missing`);
  }
  assert.doesNotMatch(acceptance, /--env-file/u);
  assert.doesNotMatch(acceptance, /--publish|-p["']/u);
});
