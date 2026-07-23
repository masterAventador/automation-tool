import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

test("IM-03 accepts only the unified Rust bootstrap's empty render-browser field", async () => {
  const [rust, gateway, gatewayTest] = await Promise.all([
    readFile(
      new URL(
        "frontend/src-tauri/src/local_video_orchestrator.rs",
        repositoryRoot,
      ),
      "utf8",
    ),
    readFile(
      new URL("workers/material_montage/gateway.py", repositoryRoot),
      "utf8",
    ),
    readFile(
      new URL("scripts/test_material_video_gateway.py", repositoryRoot),
      "utf8",
    ),
  ]);

  assert.match(rust, /render_browser: Option<VideoWorkerRenderBrowserBootstrap/u);
  assert.match(gateway, /"renderBrowser",/u);
  assert.match(gateway, /value\.get\("renderBrowser"\) is not None/u);
  assert.match(gatewayTest, /"renderBrowser": None/u);
  assert.match(gatewayTest, /\{"renderBrowser": \{\}\}/u);
});
