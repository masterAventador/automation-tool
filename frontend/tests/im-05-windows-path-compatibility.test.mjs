import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

test("IM-05 normalizes canonical Windows paths only at the upstream boundary", async () => {
  const [runtime, workerTest] = await Promise.all([
    readFile(
      new URL("workers/material_montage/webui_runtime.py", repositoryRoot),
      "utf8",
    ),
    readFile(
      new URL("scripts/test_material_video_worker.py", repositoryRoot),
      "utf8",
    ),
  ]);

  assert.match(runtime, /def _native_path_for_upstream\(path: Path\)/u);
  assert.match(runtime, /value\.startswith\("\\\\\\\\\?\\\\UNC\\\\"\)/u);
  assert.match(runtime, /value\.startswith\("\\\\\\\\\?\\\\"\)/u);
  assert.match(
    runtime,
    /_native_path_for_upstream\(runtime_root\.resolve\(strict=True\)\)/u,
  );
  assert.match(
    runtime,
    /_native_path_for_upstream\(output_root\.resolve\(strict=True\)\)/u,
  );
  assert.match(workerTest, /Windows extended-path boundary/u);
  assert.doesNotMatch(runtime, /AUTOMATION_TOOL_IM05_DEBUG|stdout=None/u);
});
