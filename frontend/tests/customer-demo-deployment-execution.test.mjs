import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function read(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("C10-08 deploys one migration job, one Control Plane, and one HTTPS ingress", async () => {
  const [composeSource, planSource] = await Promise.all([
    read("deploy/customer-demo/compose.v1.json"),
    read("deploy/customer-demo/release-plan.v1.json"),
  ]);
  const compose = JSON.parse(composeSource);
  const plan = JSON.parse(planSource);

  assert.deepEqual(Object.keys(compose.services).sort(), [
    "control-plane",
    "ingress",
    "migration",
  ]);
  assert.deepEqual(compose.services.migration.profiles, ["migration"]);
  assert.equal(compose.services.migration.restart, "no");
  assert.equal(compose.services["control-plane"].deploy.mode, "replicated");
  assert.equal(compose.services["control-plane"].deploy.replicas, 1);
  assert.equal(compose.services["control-plane"].environment.AUTOMATION_TOOL_WORKERS, "1");
  assert.equal(compose.services["control-plane"].read_only, true);
  assert.equal(compose.services["control-plane"].ports, undefined);
  assert.deepEqual(compose.services.ingress.networks, ["application"]);
  assert.equal(compose.networks.application.external, true);
  assert.equal(compose.networks.database.external, true);
  assert.equal(compose.volumes.runtime_secrets.external, true);
  assert.equal(compose.volumes.migration_secrets.external, true);
  assert.equal(compose.volumes.tls_secrets.external, true);
  assert.deepEqual(plan.phases, [
    "preflight",
    "verified_backup",
    "migration",
    "single_control_plane",
    "https_ingress",
    "health_and_version",
  ]);
  assert.equal(plan.automaticScaling, false);
  assert.equal(plan.maximumControlPlaneReplicas, 1);
  assert.equal(plan.databaseDowngrade, "forbidden");
});

test("C10-08 deployment runner is serial, digest-bound, and executable in rehearsal", async () => {
  const [deployment, acceptance, packageSource] = await Promise.all([
    read("scripts/deploy_customer_demo.py"),
    read("scripts/run_c10_08_acceptance.py"),
    read("frontend/package.json"),
  ]);

  for (const marker of [
    "backup_receipt",
    "migration",
    "control-plane",
    "ingress",
    "Health",
    "PortBindings",
    "NetworkSettings",
    "expected_app_version",
    "expected_vcs_ref",
  ]) {
    assert.ok(deployment.includes(marker), `${marker} is missing`);
  }
  assert.match(deployment, /O_EXCL/u);
  assert.match(deployment, /@sha256:/u);
  assert.match(deployment, /deployment environment syntax is invalid/u);
  assert.match(deployment, /deployment environment value is invalid/u);
  assert.match(deployment, /deployment environment number is invalid/u);
  assert.doesNotMatch(deployment, /--scale|replicas\s*[=:]\s*[2-9]/u);
  for (const marker of [
    "pg_dump",
    "alembic_version",
    "AUTOMATION_TOOL_RUNTIME_SECRET_MODE=files",
    "replicas",
    "hostPorts",
    "healthStatus",
    "versionStatus",
  ]) {
    assert.ok(acceptance.includes(marker), `${marker} is missing`);
  }
  assert.match(packageSource, /test:c10-08-deployment/u);
});
