import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);
const contractUrl = new URL(
  "contracts/deployment/customer-demo-deployment.v1.json",
  repositoryRoot,
);
const designUrl = new URL("docs/customer-demo-deployment-design.md", repositoryRoot);

async function readContract() {
  return JSON.parse(await readFile(contractUrl, "utf8"));
}

test("C10-01 freezes one public ingress and one Control Plane replica", async () => {
  const [contract, backendArchitecture] = await Promise.all([
    readContract(),
    readFile(new URL("docs/backend-architecture.md", repositoryRoot), "utf8"),
  ]);

  assert.equal(contract.version, "customer-demo-deployment.v1");
  assert.equal(contract.scope, "customer-demo-v1");
  assert.equal(contract.availability.mode, "single_instance_no_automatic_failover");
  assert.equal(contract.controlPlane.replicas, 1);
  assert.equal(contract.controlPlane.autoScaling, false);
  assert.equal(contract.controlPlane.healthPath, "/api/v1/health");
  assert.deepEqual(contract.ingress.publicTcpPorts, [443]);
  assert.equal(contract.ingress.httpRedirectPort, 80);
  assert.equal(contract.ingress.tlsMinimumVersion, "1.2");
  assert.equal(contract.ingress.controlPlaneDirectlyPublic, false);
  assert.equal(contract.desktopApp.baseUrlPattern, "https://api.<customer-domain>");
  assert.match(backendArchitecture, /customer-demo-deployment\.v1\.json/u);
});

test("C10-01 isolates PostgreSQL and secrets from public and image boundaries", async () => {
  const contract = await readContract();

  assert.equal(contract.postgresql.primaryCount, 1);
  assert.equal(contract.postgresql.publiclyReachable, false);
  assert.equal(contract.postgresql.applicationRoleSuperuser, false);
  assert.equal(contract.postgresql.tlsRequired, true);
  assert.equal(contract.postgresql.migrationsRunByApplicationStartup, false);
  assert.deepEqual(contract.network.publicServices, ["https_ingress"]);
  assert.deepEqual(contract.network.privateServices, ["control_plane", "postgresql"]);
  assert.deepEqual(contract.secrets.forbiddenLocations, [
    "git",
    "container_image",
    "compose_manifest",
    "process_arguments",
    "logs",
    "desktop_webview",
  ]);
  assert.ok(contract.secrets.required.length >= 6);
});

test("C10-01 sets finite capacity recovery and rollout boundaries", async () => {
  const [contract, design] = await Promise.all([
    readContract(),
    readFile(designUrl, "utf8"),
  ]);

  assert.deepEqual(contract.resources.controlPlane, {
    cpuLimit: "1",
    memoryLimitMiB: 1024,
    workers: 1,
    gracefulShutdownSeconds: 30,
  });
  assert.deepEqual(contract.resources.postgresql, {
    cpuLimit: "1",
    memoryLimitMiB: 2048,
    storageGiB: 20,
    maxConnections: 50,
  });
  assert.equal(contract.capacity.concurrentDesktopApps, 5);
  assert.equal(contract.capacity.concurrentRunningTasks, 1);
  assert.equal(contract.recovery.rpoHours, 24);
  assert.equal(contract.recovery.rtoHours, 4);
  assert.equal(contract.recovery.backupRetentionDays, 7);
  assert.equal(contract.recovery.restoreDrillRequiredBeforeDemo, true);
  assert.deepEqual(contract.rollout.order, [
    "backup",
    "migration",
    "control_plane",
    "health_check",
    "desktop_compatibility_check",
  ]);
  assert.equal(contract.rollout.automaticDatabaseRollback, false);

  for (const heading of [
    "## 拓扑与信任边界",
    "## 容量与资源预算",
    "## 备份、恢复与故障处理",
    "## 发布与回滚顺序",
    "## 后续任务归属",
  ]) {
    assert.ok(design.includes(heading), `${heading} is missing`);
  }
});
