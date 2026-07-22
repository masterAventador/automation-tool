import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);
const repositoryRoot = new URL("../../", import.meta.url);

test("P9-03 keeps the production macOS candidate least-privilege and bundles one signed Executor", async () => {
  const [productionConfigSource, candidateConfigSource, capabilitySource, libSource, runnerSource] =
    await Promise.all([
      readFile(new URL("src-tauri/tauri.conf.json", frontendRoot), "utf8"),
      readFile(new URL("src-tauri/tauri.macos-candidate.conf.json", frontendRoot), "utf8"),
      readFile(new URL("src-tauri/capabilities/main.json", frontendRoot), "utf8"),
      readFile(new URL("src-tauri/src/lib.rs", frontendRoot), "utf8"),
      readFile(new URL("scripts/run_p9_03_acceptance.py", repositoryRoot), "utf8"),
    ]);
  const production = JSON.parse(productionConfigSource);
  const candidate = JSON.parse(candidateConfigSource);
  const capability = JSON.parse(capabilitySource);

  assert.equal(production.app.withGlobalTauri, false);
  assert.deepEqual(production.app.security.capabilities, ["main"]);
  assert.equal(production.app.security.dangerousDisableAssetCspModification, false);
  assert.equal(production.app.security.csp["default-src"], "'self'");
  assert.equal(production.app.security.csp["connect-src"], "ipc: http://ipc.localhost");
  assert.deepEqual(capability.permissions, []);

  assert.deepEqual(candidate.bundle.targets, ["app", "dmg"]);
  assert.equal(candidate.bundle.macOS, undefined);
  assert.equal(candidate.app, undefined);
  assert.equal(candidate.plugins, undefined);

  assert.match(libSource, /resource_dir\(\)/u);
  assert.match(libSource, /initialize_with_package_root/u);
  assert.match(libSource, /local-executor/u);
  assert.match(libSource, /package/u);

  assert.match(runnerSource, /build_macos_executor_candidate/u);
  assert.match(runnerSource, /write_signed_executor_manifest/u);
  assert.match(runnerSource, /local-executor\/package/u);
  assert.match(runnerSource, /signingIdentity["']\s*:\s*["']-["']/u);
  assert.match(runnerSource, /--bundles[\s\S]*app[\s\S]*dmg/u);
  assert.match(runnerSource, /codesign[\s\S]*--deep[\s\S]*--strict/u);
  assert.match(runnerSource, /hdiutil[\s\S]*verify/u);
  assert.doesNotMatch(runnerSource, /desktop-e2e|control-plane-e2e|AUTOMATION_TOOL_UPDATE_INSTALL_PROBE/u);
});
