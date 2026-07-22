import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);
const frontendRoot = new URL("frontend/", repositoryRoot);

test("H8-19 initializes one generic policy service from the real Tauri startup path", async () => {
  const [nativeEntry, policy] = await Promise.all([
    readFile(new URL("src-tauri/src/lib.rs", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/src/app_update_policy.rs", frontendRoot), "utf8"),
  ]);

  assert.match(
    nativeEntry,
    /app\.manage\(app_update_policy::UpdatePolicyService::initialize\([\s\S]*app_data_directory[\s\S]*package_info\(\)\.version[\s\S]*DEFAULT_UPDATE_CHANNEL/u,
  );
  assert.match(policy, /UPDATE_POLICY_DIRECTORY:\s*&str\s*=\s*"app-updates"/u);
  assert.match(policy, /UPDATE_POLICY_FILE:\s*&str\s*=\s*"update-policy-v1"/u);
  assert.match(policy, /AppDataSecretStore/u);
  assert.match(policy, /saved_document/u);
  assert.doesNotMatch(policy, /keychain|credential manager|download_url|signature\s*:/iu);
});

test("H8-19 keeps the policy machine closed, monotonic and business agnostic", async () => {
  const policy = await readFile(
    new URL("src-tauri/src/app_update_policy.rs", frontendRoot),
    "utf8",
  );

  for (const action of [
    "Prompt",
    "Deferred",
    "Skipped",
    "Suppressed",
    "InstallRequested",
    "Forced",
  ]) {
    assert.match(policy, new RegExp(`\\b${action}\\b`, "u"));
  }
  assert.match(policy, /release_version\s*<=\s*minimum/u);
  assert.match(policy, /release_version\s*<\s*observed_version/u);
  assert.match(policy, /observed\s*!=\s*&identity/u);
  assert.match(policy, /UpdatePolicy::Forced/u);
  assert.match(policy, /UpdateDecision::Defer/u);
  assert.match(policy, /UpdateDecision::SkipVersion/u);
  const production = policy.split("#[cfg(test)]", 1)[0];
  for (const forbidden of ["douyin", "xiaohongshu", "taskId", "customerId"]) {
    assert.doesNotMatch(production, new RegExp(forbidden, "iu"));
  }
});

test("H8-19 validates the production startup wiring through an isolated hidden App", async () => {
  const [packageManifest, tauriConfig, wdioConfig, spec, nativeEntry] = await Promise.all([
    readFile(new URL("package.json", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/tauri.update-policy-e2e.conf.json", frontendRoot), "utf8"),
    readFile(new URL("wdio.update-policy.conf.ts", frontendRoot), "utf8"),
    readFile(new URL("e2e-tauri/update-policy.spec.ts", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/src/lib.rs", frontendRoot), "utf8"),
  ]);

  assert.match(packageManifest, /build:tauri:update-policy-test/u);
  assert.match(packageManifest, /test:h8-19-app/u);
  const configuration = JSON.parse(tauriConfig);
  assert.equal(configuration.identifier, "com.aventador.automationtool.h819acceptance");
  assert.equal(configuration.app.windows[0].visible, false);
  assert.match(wdioConfig, /update-policy\.spec\.ts/u);
  assert.match(wdioConfig, /onPrepare/u);
  assert.match(wdioConfig, /onComplete/u);
  assert.match(spec, /get_update_policy_record_for_acceptance/u);
  assert.match(nativeEntry, /fn get_update_policy_record_for_acceptance/u);
  assert.match(nativeEntry, /app_update_policy::UpdatePolicyService/u);
});
