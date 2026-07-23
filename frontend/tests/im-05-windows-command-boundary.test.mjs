import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

test("IM-05 resolves the pnpm command explicitly on Windows", async () => {
  const acceptance = await readFile(
    new URL("scripts/run_im_05_acceptance.py", repositoryRoot),
    "utf8",
  );

  assert.match(acceptance, /command = "pnpm\.cmd" if os\.name == "nt"/u);
  assert.match(acceptance, /resolved = shutil\.which\(command\)/u);
  assert.match(acceptance, /pnpm_executable\(\), "build:tauri:video-studio-test"/u);
  assert.match(acceptance, /pnpm_executable\(\),\s*"exec"/u);
  assert.match(acceptance, /pnpm_executable\(\), "build"/u);
  assert.doesNotMatch(acceptance, /\["pnpm"/u);
});
