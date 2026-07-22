import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function read(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("C10-10 fixes a bounded single-instance recovery drill", async () => {
  const plan = JSON.parse(await read("deploy/customer-demo/recovery-plan.v1.json"));

  assert.equal(plan.version, "customer-demo-recovery-plan.v1");
  assert.equal(plan.controlPlaneRestarts, 1);
  assert.equal(plan.applicationNetworkFlaps, 2);
  assert.equal(plan.maximumControlPlaneReplicas, 1);
  assert.equal(plan.maximumIngressReplicas, 1);
  assert.equal(plan.automaticScaling, false);
  assert.deepEqual(plan.protocolRecovery, [
    "executor_reconnect",
    "durable_outbox_replay",
    "last_event_id_continuation",
  ]);
});

test("C10-10 executes recovery inside C10-08 and replays production protocol recovery", async () => {
  const [deployment, recovery, packageSource] = await Promise.all([
    read("scripts/run_c10_08_acceptance.py"),
    read("scripts/run_c10_10_acceptance.py"),
    read("frontend/package.json"),
  ]);

  assert.match(deployment, /DeploymentRecoveryContext/u);
  assert.match(deployment, /recovery_probe/u);
  assert.match(deployment, /timeout=timeout_seconds/u);
  for (const marker of [
    '"restart"',
    '"disconnect"',
    '"connect"',
    '"--ip"',
    "test_control_plane_restart_reconnects_and_replays_exact_durable_outbox",
    "test_abnormal_network_disconnect_reconnects_and_replays_exact_durable_outbox",
    "test_failure_after_stream_start_closes_for_safe_last_event_reconnect",
    "Last-Event-ID",
  ]) {
    assert.ok(recovery.includes(marker), `${marker} is missing`);
  }
  assert.doesNotMatch(recovery, /--scale/u);
  assert.match(packageSource, /test:c10-10-recovery/u);
});
