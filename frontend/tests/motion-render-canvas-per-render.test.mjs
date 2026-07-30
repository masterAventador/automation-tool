import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

const SOURCES = {
  contract: "contracts/video/motion-render-canvas.v1.json",
  worker: "workers/motion_composition/worker.mjs",
  rust: "frontend/src-tauri/src/local_video_orchestrator.rs",
};

async function readSources() {
  const entries = await Promise.all(
    Object.entries(SOURCES).map(async ([name, relative]) => [
      name,
      await readFile(new URL(relative, repositoryRoot), "utf8"),
    ]),
  );
  return Object.fromEntries(entries);
}

/**
 * PC-05: a catalog part is not drawn on the template's stage.
 *
 * The canvas was one fixed pair of numbers because one thing was ever rendered:
 * `composition_template`'s output, whose whole type scale is written for
 * 640x360. A catalog part is an independent composition that declares its own
 * stage — measured on the frozen catalog, 105 parts are 1920x1080, three are
 * 1080x1920 portrait and one is 1440x2560. Rendering any of them at 640x360
 * captures the top-left corner of their stage, which is precisely the incident
 * this contract's own `rationale.problem` records: 180 byte-identical frames
 * encoded into a valid MP4 that was a still image, with neither the authoring
 * gates nor the sandbox able to see the disagreement.
 *
 * So the canvas travels with the render request. These assertions keep the two
 * ends from drifting: the bounds live in the contract, and neither the Worker
 * nor the Rust caller may restate a viewport of its own.
 */
test("the render canvas is declared per render, not fixed in the Worker", async () => {
  const sources = await readSources();
  const contract = JSON.parse(sources.contract);

  assert.ok(
    contract.requestedCanvas,
    "the contract must declare what a render request may ask for",
  );
  for (const field of ["widthMinimum", "widthMaximum", "heightMinimum", "heightMaximum"]) {
    assert.equal(
      typeof contract.requestedCanvas[field],
      "number",
      `requestedCanvas.${field} must be a number`,
    );
  }
  // The template canvas stays declared: it is what `composition_template`
  // renders on, and the authoring gate still compares against it.
  assert.equal(contract.width, 640);
  assert.equal(contract.height, 360);

  // The Worker reads the canvas out of the spec it is handed.
  assert.match(sources.worker, /"canvas"/);
  assert.doesNotMatch(
    sources.worker,
    /const RENDER_VIEWPORT_WIDTH = \d+/,
    "the Worker must not keep a viewport constant of its own",
  );

  // And the Rust caller sends one rather than relying on a default.
  assert.match(sources.rust, /"canvas"/);
});

test("a part's declared stage is inside what a render may request", async () => {
  const sources = await readSources();
  const contract = JSON.parse(sources.contract);
  const catalog = JSON.parse(
    await readFile(new URL("contracts/quality/motion-catalog.v1.json", repositoryRoot), "utf8"),
  );

  const declared = catalog.items
    .map((item) => item.dimensions)
    .filter((dimensions) => dimensions && dimensions.width && dimensions.height);
  assert.ok(declared.length > 100, "the catalog should declare stages for most parts");

  const bounds = contract.requestedCanvas;
  for (const { width, height } of declared) {
    assert.ok(
      width >= bounds.widthMinimum && width <= bounds.widthMaximum,
      `a part declares width ${width}, outside the requestable range`,
    );
    assert.ok(
      height >= bounds.heightMinimum && height <= bounds.heightMaximum,
      `a part declares height ${height}, outside the requestable range`,
    );
  }
});
