import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function read(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("C10-12 freezes the ordered customer journey and evidence boundary", async () => {
  const contract = JSON.parse(
    await read("contracts/quality/customer-demo-acceptance-v1.json"),
  );

  assert.equal(contract.version, "customer-demo-acceptance.v1");
  assert.deepEqual(
    contract.journey.map(({ id }) => id),
    [
      "install",
      "account_login",
      "automatic_device_binding",
      "workbench",
      "platform_scan",
      "target_preview",
      "controlled_action",
      "structured_results",
      "manual_handoff",
    ],
  );
  assert.ok(contract.journey.every(({ evidence }) => evidence.length > 0));
  assert.equal(contract.safety.externalPlatformWrites, 0);
  assert.equal(contract.safety.automaticChallengeBypass, false);
  assert.equal(contract.safety.realCustomerCredentials, false);
  assert.deepEqual(contract.profiles, ["isolated_full", "device_package"]);
});

test("C10-12 has one executable full-isolated runner and device handoff", async () => {
  const [runner, packageSource] = await Promise.all([
    read("scripts/run_c10_12_acceptance.py"),
    read("frontend/package.json"),
  ]);

  for (const marker of [
    "run_u9_06_acceptance.py",
    "run_h8_16f_acceptance.py",
    "test_douyin_qr_login_browser.py",
    "test_task_discovery_lifecycle.py",
    "--full-isolated-app",
    "--device-package-handoff",
  ]) {
    assert.ok(runner.includes(marker), `${marker} is missing`);
  }
  assert.match(packageSource, /test:c10-12-demo/u);
});
