import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
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
    /let update_policy\s*=\s*std::sync::Arc::new\([\s\S]*app_update_policy::UpdatePolicyService::initialize\([\s\S]*app_data_directory[\s\S]*package_info\(\)\.version[\s\S]*DEFAULT_UPDATE_CHANNEL[\s\S]*app\.manage\(std::sync::Arc::clone\(&update_policy\)\)/u,
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

test("H8-19 does not claim UI coverage through a zero-click WDIO surrogate", async () => {
  const packageJson = JSON.parse(
    await readFile(new URL("package.json", frontendRoot), "utf8"),
  );

  assert.equal(packageJson.scripts["build:tauri:update-policy-test"], undefined);
  assert.equal(packageJson.scripts["test:h8-19-app"], undefined);
  await Promise.all([
    assert.rejects(access(new URL("wdio.update-policy.conf.ts", frontendRoot)), {
      code: "ENOENT",
    }),
    assert.rejects(access(new URL("e2e-tauri/update-policy.spec.ts", frontendRoot)), {
      code: "ENOENT",
    }),
  ]);
});
