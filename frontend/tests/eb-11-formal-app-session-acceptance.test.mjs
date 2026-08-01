import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);
const repositoryRoot = new URL("../../", import.meta.url);

test("EB-11 exposes one explicit formal-App real-session acceptance command", async () => {
  const [packageSource, runnerSource] = await Promise.all([
    readFile(new URL("package.json", frontendRoot), "utf8"),
    readFile(
      new URL("scripts/run_eb_11_formal_app_acceptance.py", repositoryRoot),
      "utf8",
    ),
  ]);
  const packageDocument = JSON.parse(packageSource);

  assert.equal(
    packageDocument.scripts["test:eb11-macos-formal-session"],
    "../backend/.venv/bin/python ../scripts/run_eb_11_formal_app_acceptance.py --interactive-device-acceptance",
  );
  assert.match(runnerSource, /--interactive-device-acceptance/u);
  assert.doesNotMatch(runnerSource, /--expected-bundle-tree-sha256/u);
  assert.doesNotMatch(runnerSource, /--expected-executor-build-id/u);
  assert.match(runnerSource, /--deployment-profile/u);
  assert.match(runnerSource, /AutomationToolReleaseIdentity/u);
  assert.match(runnerSource, /repository_source_facts/u);
  assert.match(runnerSource, /sys\.platform\s*!=\s*["']darwin["']/u);
  assert.match(runnerSource, /isatty\(\)/u);
  assert.match(runnerSource, /com\.aventador\.automationtool/u);
});

test("EB-11 drives the full normal account page login lifecycle", async () => {
  const runner = await readFile(
    new URL("scripts/run_eb_11_formal_app_acceptance.py", repositoryRoot),
    "utf8",
  );

  for (const marker of [
    "账号与平台",
    "我已处理，重新检查",
    "打开登录处理",
    "安全注销",
    "确认注销",
    "需要登录",
    "douyin_scan_confirmed",
    "登录正常",
    "process_unavailable",
    "firstVisibleObservedAtBefore",
    "firstVisibleObservedAtAfter",
    "restartVisibleObservedAtBefore",
    "restartVisibleObservedAtAfter",
    "restartSessionReused",
    "safeLogoutObserved",
    "require_safe_logout_cleanup",
    "require_same_profile_reuse",
    "realQrLoginConfirmed",
    "ownedProcessTreeResidualCount",
    "bundleTreeSha256",
    "bundleCdHash",
    "executorBuildId",
    "firstPackagedExecutorObserved",
    "firstEmbeddedChromiumObserved",
    "firstAppOwnedProfileObserved",
    "verify_running_release_process",
    "restartPackagedExecutorObserved",
    "restartEmbeddedChromiumObserved",
    "restartAppOwnedProfileObserved",
  ]) {
    assert.match(runner, new RegExp(marker, "u"));
  }
  assert.match(runner, /codesign/u);
  assert.match(runner, /spctl/u);
  assert.match(runner, /stapler/u);
  assert.match(runner, /macos-release-signing\.v1\.json/u);
  assert.match(runner, /expected_certificate/u);
  assert.match(runner, /expected_team/u);
  assert.match(runner, /hashlib\.sha256/u);
  assert.match(runner, /O_NOFOLLOW/u);
  assert.match(runner, /mode=0o600/u);
  assert.match(runner, /application process whose unix id is/u);
  assert.match(runner, /descendant_records/u);
  assert.match(runner, /cleanup_owned_runtime/u);
  assert.match(runner, /input\(/u);
  assert.match(runner, /embedded-browser-profiles/u);
  assert.match(runner, /distribution-manifest\.v1\.json/u);
  assert.match(runner, /executor-manifest\.v1\.json/u);
  assert.doesNotMatch(runner, /tell application id .* to quit/iu);
  assert.doesNotMatch(runner, /Default\/Cookies|storage_state|document\.cookie/iu);
  assert.doesNotMatch(runner, /wdio|webdriver|control-plane-e2e|headless/iu);

  const acceptanceBody = runner.slice(runner.indexOf("def run_acceptance"));
  assert.equal(
    [...acceptanceBody.matchAll(/verify_release_artifact\(/gu)].length,
    2,
    "the full signed App identity must be verified before launch and again before evidence",
  );
  assert.equal(
    [...acceptanceBody.matchAll(/recheck_healthy_session\(/gu)].length,
    3,
    "the normal App action must prove the old session, the new QR login, and restart reuse",
  );
  assert.equal(
    [...acceptanceBody.matchAll(/logout_current_session\(/gu)].length,
    1,
    "the formal App must safely log out through the normal page",
  );
  assert.equal(
    [...acceptanceBody.matchAll(/require_safe_logout_cleanup\(/gu)].length,
    1,
    "safe logout must remove the exact Profile observed through packaged Chromium",
  );
  assert.equal(
    [...acceptanceBody.matchAll(/open_login_for_scan\(/gu)].length,
    1,
    "the formal App must open a real QR login through the normal page",
  );
  assert.equal(
    [...acceptanceBody.matchAll(/verify_running_release_process\(/gu)].length,
    4,
    "both launched processes must stay bound to the signed release across user interaction",
  );
});
