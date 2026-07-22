import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);
const repositoryRoot = new URL("../../", import.meta.url);

test("P9-02 owns a fail-closed native Windows Executor candidate acceptance", async () => {
  const [packageSource, pyprojectSource, runnerSource, workflowSource] = await Promise.all([
    readFile(new URL("package.json", frontendRoot), "utf8"),
    readFile(new URL("backend/pyproject.toml", repositoryRoot), "utf8"),
    readFile(new URL("scripts/run_p9_02_acceptance.py", repositoryRoot), "utf8"),
    readFile(new URL(".github/workflows/desktop.yml", repositoryRoot), "utf8"),
  ]);
  const packageDocument = JSON.parse(packageSource);

  assert.equal(
    packageDocument.scripts["test:p9-02-windows-executor"],
    "uv run --project ../backend --locked python ../scripts/run_p9_02_acceptance.py",
  );
  assert.match(
    pyprojectSource,
    /automation-tool-build-windows-executor\s*=\s*"automation_tool\.executor\.windows_candidate:main"/u,
  );
  assert.match(runnerSource, /sys\.platform\s*!=\s*["']win32["']/u);
  assert.match(runnerSource, /build_windows_executor_candidate/u);
  assert.match(runnerSource, /write_signed_executor_manifest/u);
  assert.match(runnerSource, /target_platform=["']windows["']/u);
  assert.match(runnerSource, /UIAutomationClient/u);
  assert.match(runnerSource, /run_e4_09_acceptance\.py/u);
  assert.match(runnerSource, /input=b["']["']/u);
  assert.doesNotMatch(runnerSource, /subprocess\.(?:Popen|run)\([^)]*(?:chrome|edge)/iu);
  assert.match(workflowSource, /test_windows_candidate\.py/u);
  assert.match(workflowSource, /run_p9_02_acceptance\.py/u);
  assert.match(workflowSource, /runner\.os\s*==\s*'Windows'/u);
});
