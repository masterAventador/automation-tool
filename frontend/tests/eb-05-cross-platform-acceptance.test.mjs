import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

test("EB-05 drives the distribution acceptance for the native target", async () => {
  const acceptance = await readFile(
    new URL("scripts/run_eb_05_acceptance.py", repositoryRoot),
    "utf8",
  );

  for (const target of [
    "macos-arm64",
    "macos-x86_64",
    "windows-x86_64",
  ]) {
    assert.match(acceptance, new RegExp(`"${target}"`, "u"));
  }
  assert.match(acceptance, /ROOT \/ "\.local\/eb-04-windows\/chrome-win64\.zip"/u);
  assert.match(acceptance, /target = contract\.targets\[target_id\]/u);
  assert.match(acceptance, /target_id=target_id/u);
  assert.match(
    acceptance,
    /tampered = staging \/ Path\(\*target\.executable\.split\("\/"\)\)/u,
  );
  assert.doesNotMatch(acceptance, /must run on macOS arm64/u);
});
