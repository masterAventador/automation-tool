import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

test("EB-04 locks and accepts the Windows x86_64 Chromium staging target", async () => {
  const [contractText, runner] = await Promise.all([
    readFile(
      new URL(
        "contracts/browser/embedded-chromium-staging.v1.json",
        repositoryRoot,
      ),
      "utf8",
    ),
    readFile(
      new URL("scripts/run_eb_04_acceptance.py", repositoryRoot),
      "utf8",
    ),
  ]);
  const contract = JSON.parse(contractText);
  const target = contract.targets["windows-x86_64"];

  assert.equal(target.buildable, true);
  assert.equal("pending_reason" in target, false);
  assert.match(target.archive_sha256, /^[0-9a-f]{64}$/u);
  assert.equal(target.root_entry, "chrome-win64");
  assert.equal(target.executable, "chrome-win64/chrome.exe");
  assert.match(runner, /sys\.platform != "win32"/u);
  assert.match(runner, /0x8664/u);
  assert.match(runner, /offline=True/u);
  assert.match(runner, /manifest_a != manifest_b/u);
  assert.match(runner, /processes_for_executable/u);
  assert.match(runner, /Windows staging acceptance passed/u);
});
