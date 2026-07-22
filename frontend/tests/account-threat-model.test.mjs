import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);
const contractUrl = new URL("contracts/security/account-threat-model-v1.json", repositoryRoot);

test("U9-01 freezes the closed Demo account scope and lifecycle", async () => {
  const [contractSource, productPlan, backendArchitecture, roadmap] = await Promise.all([
    readFile(contractUrl, "utf8"),
    readFile(new URL("docs/product-plan.md", repositoryRoot), "utf8"),
    readFile(new URL("docs/backend-architecture.md", repositoryRoot), "utf8"),
    readFile(new URL("docs/development-roadmap.md", repositoryRoot), "utf8"),
  ]);
  const contract = JSON.parse(contractSource);

  assert.equal(contract.version, "account.threat-model.v1");
  assert.equal(contract.release, "customer-demo-v1");
  assert.equal(contract.provisioning.mode, "authenticated_operations_only");
  assert.equal(contract.provisioning.publicSignup, false);
  assert.deepEqual(contract.account.lifecycle, ["active", "locked", "disabled"]);
  assert.deepEqual(contract.account.loginIdentifier, {
    kind: "login_name",
    canonicalPattern: "^[a-z][a-z0-9._-]{2,63}$",
    caseSensitive: false,
    mutable: false,
  });
  assert.equal(contract.account.credentials.kind, "password");
  assert.equal(contract.account.credentials.passwordHash, "argon2id");
  assert.equal(contract.account.credentials.pepperRequired, true);
  assert.equal(contract.account.recovery.mode, "operations_issued_one_time_token");
  assert.equal(contract.account.recovery.publicRequestEndpoint, false);

  assert.deepEqual(contract.excluded, [
    "anonymous_signup",
    "organization",
    "tenant",
    "rbac",
    "subscription",
    "billing",
    "social_login",
    "account_deletion",
  ]);
  assert.match(productPlan, /客户 Demo 启动时未登录只显示登录与恢复状态/u);
  assert.match(productPlan, /不使用匿名设备申请、配对码、设备轮询或后台逐设备审批/u);
  assert.match(backendArchitecture, /account-threat-model-v1\.json/u);
  assert.match(roadmap, /\| U9-01 \|[^\n]+\| ✅ 已完成 \|/u);
});

test("U9-01 makes sessions ownership revocation and audit fail closed", async () => {
  const contract = JSON.parse(await readFile(contractUrl, "utf8"));

  assert.deepEqual(contract.sessions.tokenKinds, ["access", "refresh"]);
  assert.equal(contract.sessions.transport, "authorization_header_only");
  assert.equal(contract.sessions.serverStorage, "digest_only");
  assert.equal(contract.sessions.accessLifetimeSeconds, 600);
  assert.equal(contract.sessions.refreshAbsoluteLifetimeSeconds, 2592000);
  assert.equal(contract.sessions.refreshRotation, "single_use_family");
  assert.equal(contract.sessions.refreshReuseResponse, "revoke_family");
  assert.equal(contract.sessions.reactSecretExposure, false);

  assert.equal(contract.deviceOwnership.userToInstallation, "one_to_many");
  assert.equal(contract.deviceOwnership.installationToUser, "exactly_one_immutable_owner");
  assert.equal(contract.deviceOwnership.binding, "account_session_plus_device_key_proof");
  assert.equal(contract.deviceOwnership.crossAccountRebinding, "rejected");
  assert.deepEqual(contract.authorization.businessRequestRequires, [
    "active_account_session",
    "active_owned_installation",
    "required_capability",
  ]);
  assert.deepEqual(contract.revocation.accountDisable, [
    "reject_login_and_refresh",
    "revoke_all_account_sessions",
    "reject_device_session_exchange",
  ]);
  assert.deepEqual(contract.revocation.deviceRevoke, [
    "revoke_device_credentials_and_sessions",
    "preserve_other_owned_devices",
  ]);

  assert.ok(contract.audit.events.length >= 12);
  assert.equal(new Set(contract.audit.events).size, contract.audit.events.length);
  assert.deepEqual(contract.audit.forbiddenFields, [
    "password",
    "token",
    "credential",
    "raw_login_identifier",
    "raw_ip_address",
    "raw_user_agent",
    "platform_cookie",
  ]);
});

test("U9-01 covers every named threat with explicit controls", async () => {
  const contract = JSON.parse(await readFile(contractUrl, "utf8"));
  const expectedThreats = [
    "account_enumeration",
    "credential_stuffing",
    "brute_force",
    "password_database_theft",
    "recovery_token_theft_or_replay",
    "access_token_theft",
    "refresh_token_replay",
    "cross_account_device_binding",
    "disabled_account_continued_access",
    "secret_leakage_to_webview_or_logs",
    "platform_session_confusion",
    "operations_endpoint_abuse",
  ];

  assert.deepEqual(
    contract.threats.map(({ id }) => id),
    expectedThreats,
  );
  for (const threat of contract.threats) {
    assert.ok(threat.controls.length >= 2, `${threat.id} has insufficient controls`);
    assert.equal(new Set(threat.controls).size, threat.controls.length);
  }
  assert.ok(contract.invariants.length >= 10);
  assert.equal(new Set(contract.invariants).size, contract.invariants.length);
});
