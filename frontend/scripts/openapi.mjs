import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { normalizeGeneratedText } from "./generated-text.mjs";

const frontendRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const snapshot = join(frontendRoot, "../contracts/openapi/control-plane.v1.json");
const output = join(frontendRoot, "src/api/generated/control-plane.ts");
const check = process.argv.includes("--check");
const openapiTypescriptRoot = dirname(
  fileURLToPath(import.meta.resolve("openapi-typescript/package.json")),
);
const openapiTypescriptCli = join(openapiTypescriptRoot, "bin/cli.js");
const temporaryDirectory = await mkdtemp(join(tmpdir(), "automation-tool-openapi-"));
const temporaryOutput = join(temporaryDirectory, "control-plane.ts");

try {
  const generation = spawnSync(
    process.execPath,
    [openapiTypescriptCli, snapshot, "--output", temporaryOutput],
    {
      cwd: frontendRoot,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    },
  );

  if (generation.status !== 0) {
    const generationFailure = generation.stderr ?? "";
    process.stderr.write(generationFailure || "OpenAPI generation failed\n");
    process.exitCode = generation.status ?? 1;
  } else {
    const generated = normalizeGeneratedText(await readFile(temporaryOutput, "utf8"));
    if (check) {
      let committed = "";
      try {
        committed = normalizeGeneratedText(await readFile(output, "utf8"));
      } catch {
        // A missing generated file is ordinary drift and uses the same safe message.
      }
      if (committed !== generated) {
        process.stderr.write("Generated Control Plane DTOs are out of date\n");
        process.exitCode = 1;
      }
    } else {
      await writeFile(output, generated);
    }
  }
} finally {
  await rm(temporaryDirectory, { recursive: true, force: true });
}
