import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function read(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("C10-09 freezes one protocol corpus for local and Demo profiles", async () => {
  const [contractSource, healthSource, versionSource] = await Promise.all([
    read("contracts/deployment/customer-demo-protocol-regression.v1.json"),
    read("contracts/fixtures/control-plane-v1/health.json"),
    read("contracts/fixtures/control-plane-v1/version.json"),
  ]);
  const contract = JSON.parse(contractSource);
  const health = JSON.parse(healthSource);
  const version = JSON.parse(versionSource);

  assert.equal(contract.version, "customer-demo-protocol-regression.v1");
  assert.deepEqual(contract.profiles, ["local", "demo"]);
  assert.equal(contract.openapi, "contracts/openapi/control-plane.v1.json");
  assert.equal(contract.generatedDto, "frontend/src/api/generated/control-plane.ts");
  assert.deepEqual(contract.controlPlaneFixtures, [
    "contracts/fixtures/control-plane-v1/health.json",
    "contracts/fixtures/control-plane-v1/version.json",
  ]);
  assert.equal(contract.executorFixtures, "contracts/fixtures/executor-v1");
  assert.deepEqual(contract.allowedBuildDifferences, ["compiledDeploymentProfile"]);
  assert.deepEqual(contract.businessCodeDifferences, []);
  assert.deepEqual(health, {
    service: "control-plane",
    status: "ok",
    version: "0.1.0",
  });
  assert.equal(version.service, "control-plane");
  assert.equal(version.version, health.version);
  assert.equal(version.apiVersion, "v1");
});

test("C10-09 replays the same App protocol suite under the only allowed profile delta", async () => {
  const [runner, controlPlane, backendTest, packageSource] = await Promise.all([
    read("scripts/run_c10_09_acceptance.py"),
    read("frontend/src-tauri/src/control_plane.rs"),
    read("backend/tests/contract/test_openapi_snapshot.py"),
    read("frontend/package.json"),
  ]);

  for (const marker of [
    "customer-demo-protocol-regression.v1.json",
    "control-plane.v1.json",
    "executor-v1",
    "local",
    "demo",
    "AUTOMATION_TOOL_DEPLOYMENT_PROFILE_PAYLOAD",
    "sourceDigest",
  ]) {
    assert.ok(runner.includes(marker), `${marker} is missing`);
  }
  assert.match(controlPlane, /control-plane-v1\/health\.json/u);
  assert.match(controlPlane, /control-plane-v1\/version\.json/u);
  assert.match(controlPlane, /control-plane\.v1\.json/u);
  assert.match(backendTest, /control-plane-v1/u);
  assert.match(packageSource, /test:c10-09-protocol/u);
});
