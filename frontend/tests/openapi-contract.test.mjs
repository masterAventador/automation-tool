import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);
const frontendRoot = new URL("../", import.meta.url);

test("generated TypeScript DTOs match the committed versioned OpenAPI snapshot", async () => {
  const [snapshotText, generated, packageText, generatorScript] = await Promise.all([
    readFile(new URL("contracts/openapi/control-plane.v1.json", repositoryRoot), "utf8"),
    readFile(new URL("src/api/generated/control-plane.ts", frontendRoot), "utf8"),
    readFile(new URL("package.json", frontendRoot), "utf8"),
    readFile(new URL("scripts/openapi.mjs", frontendRoot), "utf8"),
  ]);
  const snapshot = JSON.parse(snapshotText);
  const packageJson = JSON.parse(packageText);

  assert.equal(snapshot.paths["/api/v1/health"].get.operationId, "getSystemHealth");
  assert.equal(snapshot.paths["/api/v1/version"].get.operationId, "getSystemVersion");
  assert.match(generated, /getSystemHealth/);
  assert.match(generated, /getSystemVersion/);
  assert.equal(packageJson.scripts["generate:api"], "node scripts/openapi.mjs");
  assert.equal(packageJson.scripts["check:api"], "node scripts/openapi.mjs --check");
  assert.match(generatorScript, /import\.meta\.resolve\("openapi-typescript\/package\.json"\)/);
  assert.match(generatorScript, /join\(openapiTypescriptRoot, "bin\/cli\.js"\)/);
  assert.match(generatorScript, /spawnSync\(\s*process\.execPath/);
  assert.match(generatorScript, /generation\.stderr \?\?/);
  assert.doesNotMatch(generatorScript, /pnpm\.cmd/);
});
