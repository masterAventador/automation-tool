import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

// The only path that has ever produced a complete package was a step inside an
// acceptance script, and no workflow ran that script. Three video runtime
// resources therefore reached a user's machine without a single gate objecting.
// These tests pin the two structural properties that made that possible:
// a release command must exist on its own, and CI must run the checks that can
// refuse a package built without one of the declared resources.

const repositoryFile = (name) => readFile(new URL(`../../${name}`, import.meta.url), "utf8");

test("a release command exists that is not a step inside an acceptance script", async () => {
  const command = await repositoryFile("scripts/build_release_package.py");
  // It has to assemble every resource and run the gates itself, not inherit
  // them from whatever acceptance suite happens to call it.
  assert.match(command, /prepare_video_runtime/u);
  assert.match(command, /install_video_runtime\(/u);
  assert.match(command, /install_and_seal\(/u);
  assert.match(command, /require_packaged_video_runtime\(/u);
  assert.match(command, /require_packaged_browser\(/u);
  assert.match(command, /audit-production-package\.mjs/u);
  assert.match(command, /audit-release-bundle\.mjs/u);
  assert.match(command, /--package-root/u);
  assert.match(command, /add_argument\(\s*"--platform"/u);
  const packageJson = await repositoryFile("frontend/package.json");
  assert.match(packageJson, /release:package/u);
});

test("every release path hands the audit a package root instead of a bare binary", async () => {
  for (const runner of [
    "scripts/build_release_package.py",
    "scripts/run_eb_16_acceptance.py",
    "scripts/run_eb_16_windows_acceptance.py",
  ]) {
    const source = await repositoryFile(runner);
    assert.match(
      source,
      /"--package-root"/u,
      `${runner} must let the audit see the package, not only the binary`,
    );
  }
});

test("the release resource inventory is declared once and read, never restated", async () => {
  const contract = JSON.parse(await repositoryFile("contracts/quality/release-package-resources.v1.json"));
  assert.deepEqual(
    contract.resources.map((resource) => resource.name),
    [
      "embedded-browser",
      "local-executor",
      "media-toolchain",
      "motion-video-worker",
      "material-video-worker",
    ],
  );
  const audit = await repositoryFile("frontend/scripts/audit-production-package.mjs");
  assert.match(audit, /release-package-resources\.v1\.json/u);
  // A second hand-written copy of the inventory inside the audit is exactly the
  // drift the contract exists to prevent.
  for (const name of ["media-toolchain", "motion-video-worker", "material-video-worker"]) {
    assert.doesNotMatch(
      audit,
      new RegExp(`"${name}"`, "u"),
      `${name} must come from the contract, not from a literal in the audit`,
    );
  }
});

test("desktop CI runs the release wiring gate and the package resource audit tests", async () => {
  const workflow = await repositoryFile(".github/workflows/desktop.yml");
  assert.match(
    workflow,
    /check_release_package_wiring\.py/u,
    "desktop CI must run the release wiring gate",
  );
  assert.match(
    workflow,
    /test_release_assembly\.py/u,
    "desktop CI must run the release assembler tests",
  );
  assert.match(
    workflow,
    /production-package-audit\.test\.mjs|test:contracts/u,
    "desktop CI must run the package audit tests that refuse a missing resource",
  );
  // The gate is worthless if it cannot fail: its own self-test mutates a
  // release path and requires the check to reject it.
  assert.match(workflow, /check_release_package_wiring\.py --self-test/u);
});
