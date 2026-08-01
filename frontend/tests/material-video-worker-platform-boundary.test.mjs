import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

test("IM-02 freezes platform-specific Python distributions and Windows cleanup", async () => {
  const [contractSource, builder, acceptance] = await Promise.all([
    readFile(
      new URL(
        "contracts/quality/material-video-worker-package.v1.json",
        repositoryRoot,
      ),
      "utf8",
    ),
    readFile(
      new URL(
        "scripts/build_material_video_worker_candidate.py",
        repositoryRoot,
      ),
      "utf8",
    ),
    readFile(
      new URL("scripts/run_im_02_acceptance.py", repositoryRoot),
      "utf8",
    ),
  ]);
  const contract = JSON.parse(contractSource);
  const dependencies = contract.dependencies;

  assert.deepEqual(dependencies.expectedInstalledDistributionCountByTarget, {
    "macos-arm64": 114,
    "macos-x86_64": 114,
    "windows-x86_64": 117,
  });
  assert.deepEqual(dependencies.platformRequired["windows-x86_64"], {
    colorama: "0.4.6",
    watchdog: "6.0.0",
    win32_setctime: "1.2.0",
  });
  assert.match(builder, /def current_target_id\(\)/u);
  assert.match(builder, /expected_dependency_count\(contract\)/u);
  assert.match(builder, /required_dependencies\(contract\)/u);
  assert.match(builder, /for attempt in range\(10\)/u);
  assert.match(builder, /shutil\.rmtree\(path\)/u);
  assert.match(acceptance, /expected_dependency_count\(load_contract\(\)\)/u);
  assert.doesNotMatch(acceptance, /dependency_count != 112/u);
});
