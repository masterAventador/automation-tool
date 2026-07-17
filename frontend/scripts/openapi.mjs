import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const snapshot = join(frontendRoot, "../contracts/openapi/control-plane.v1.json");
const output = join(frontendRoot, "src/api/generated/control-plane.ts");
const check = process.argv.includes("--check");
const pnpmExecutable = process.platform === "win32" ? "pnpm.cmd" : "pnpm";
const temporaryDirectory = await mkdtemp(join(tmpdir(), "automation-tool-openapi-"));
const temporaryOutput = join(temporaryDirectory, "control-plane.ts");

try {
  const generation = spawnSync(
    pnpmExecutable,
    ["exec", "openapi-typescript", snapshot, "--output", temporaryOutput],
    {
      cwd: frontendRoot,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    },
  );

  if (generation.status !== 0) {
    process.stderr.write(generation.stderr);
    process.exitCode = generation.status ?? 1;
  } else {
    const generated = await readFile(temporaryOutput, "utf8");
    if (check) {
      let committed = "";
      try {
        committed = await readFile(output, "utf8");
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
