import { spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { withoutExplicitContentLength } from "./wdio-request-compatibility";

const outputDir = mkdtempSync(join(tmpdir(), "automation-tool-wdio-"));
const cleanupScript = [
  'const { rmSync } = require("node:fs");',
  "const parentPid = Number(process.argv[1]);",
  "const outputDir = process.argv[2];",
  "const poll = () => {",
  "  try {",
  "    process.kill(parentPid, 0);",
  "    setTimeout(poll, 250);",
  "  } catch {",
  "    rmSync(outputDir, { force: true, maxRetries: 3, recursive: true, retryDelay: 50 });",
  "  }",
  "};",
  "poll();",
].join("\n");
const cleanup = spawn(
  process.execPath,
  ["--eval", cleanupScript, String(process.pid), outputDir],
  {
    detached: true,
    stdio: "ignore",
    windowsHide: true,
  },
);
cleanup.unref();

export const wdioRuntimeArtifacts = {
  outputDir,
  transformRequest: withoutExplicitContentLength,
} satisfies Pick<WebdriverIO.Config, "outputDir" | "transformRequest">;
