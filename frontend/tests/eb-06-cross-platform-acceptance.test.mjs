import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

test("EB-06 runs native synthetic and real Rust distribution tests", async () => {
  const [acceptance, macTest, windowsTest] = await Promise.all([
    readFile(
      new URL("scripts/run_eb_06_acceptance.py", repositoryRoot),
      "utf8",
    ),
    readFile(
      new URL(
        "frontend/src-tauri/tests/embedded_browser_distribution.rs",
        repositoryRoot,
      ),
      "utf8",
    ),
    readFile(
      new URL(
        "frontend/src-tauri/tests/embedded_browser_distribution_windows.rs",
        repositoryRoot,
      ),
      "utf8",
    ),
  ]);

  assert.match(macTest, /#!\[cfg\(target_os = "macos"\)\]/u);
  assert.match(windowsTest, /#!\[cfg\(windows\)\]/u);
  assert.match(
    windowsTest,
    /fn real_staged_distribution_loads_end_to_end\(\)/u,
  );
  assert.match(
    windowsTest,
    /fn windows_distribution_root_junction_is_rejected\(\)/u,
  );
  assert.match(windowsTest, /"mklink",\s*"\/J"/u);
  assert.match(acceptance, /"embedded_browser_distribution_windows"/u);
  assert.match(acceptance, /target_id=target_id/u);
  assert.match(acceptance, /test_target/u);
  assert.doesNotMatch(acceptance, /must run on macOS arm64/u);
});
