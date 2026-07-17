import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { assertProductionBoundaries } from "../scripts/check-production-boundaries.mjs";

test("production boundary scanner rejects Harness entries and markers", async () => {
  const distribution = await mkdtemp(join(tmpdir(), "automation-tool-production-boundary-"));
  const assets = join(distribution, "assets");

  try {
    await mkdir(assets);
    await writeFile(join(distribution, "index.html"), "<main>desktop</main>");
    await writeFile(join(assets, "app.js"), "console.log('desktop')");
    await assert.doesNotReject(assertProductionBoundaries(distribution));

    await writeFile(join(distribution, "harness.html"), "test-only");
    await assert.rejects(
      assertProductionBoundaries(distribution),
      /Production build contains the UI Harness entry/,
    );
    await rm(join(distribution, "harness.html"));

    await writeFile(join(assets, "app.js"), "automation-tool-test-harness-adapter");
    await assert.rejects(
      assertProductionBoundaries(distribution),
      /Production build contains a test Harness marker/,
    );

    await writeFile(join(assets, "app.js"), "window.wdioTauri = {};");
    await assert.rejects(
      assertProductionBoundaries(distribution),
      /Production build contains a desktop test marker/,
    );
  } finally {
    await rm(distribution, { recursive: true, force: true });
  }
});
