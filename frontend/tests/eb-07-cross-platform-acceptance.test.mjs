import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

test("EB-07 runs native Authority and executor request acceptance", async () => {
  const [acceptance, authority, macTest, windowsTest] = await Promise.all([
    readFile(
      new URL("scripts/run_eb_07_acceptance.py", repositoryRoot),
      "utf8",
    ),
    readFile(
      new URL(
        "frontend/src-tauri/src/embedded_browser_authority.rs",
        repositoryRoot,
      ),
      "utf8",
    ),
    readFile(
      new URL(
        "frontend/src-tauri/tests/embedded_browser_authority.rs",
        repositoryRoot,
      ),
      "utf8",
    ),
    readFile(
      new URL(
        "frontend/src-tauri/tests/embedded_browser_authority_windows.rs",
        repositoryRoot,
      ),
      "utf8",
    ),
  ]);

  assert.match(macTest, /#!\[cfg\(target_os = "macos"\)\]/u);
  assert.match(windowsTest, /#!\[cfg\(windows\)\]/u);
  assert.match(
    windowsTest,
    /distribution_root_junction_after_caching_forces_full_revalidation/u,
  );
  assert.match(authority, /cached_executable_still_sound/u);
  assert.match(acceptance, /"embedded_browser_authority_windows"/u);
  assert.match(acceptance, /contract\.targets\[target_id\]/u);
  assert.doesNotMatch(acceptance, /must run on macOS arm64/u);
});
