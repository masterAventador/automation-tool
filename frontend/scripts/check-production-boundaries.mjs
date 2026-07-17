import { readdir, readFile } from "node:fs/promises";
import { dirname, extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const textExtensions = new Set([".css", ".html", ".js", ".json"]);
const forbiddenMarkers = [
  "automation-tool-test-harness",
  "automation-tool-test-harness-adapter",
  "UI Harness root is missing",
];

async function filesUnder(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(
    entries.map((entry) => {
      const path = join(directory, entry.name);
      return entry.isDirectory() ? filesUnder(path) : [path];
    }),
  );
  return nested.flat();
}

export async function assertProductionBoundaries(distribution) {
  const files = await filesUnder(distribution);
  const relativeFiles = files.map((path) => relative(distribution, path));

  if (relativeFiles.includes("harness.html")) {
    throw new Error("Production build contains the UI Harness entry");
  }

  for (const path of files) {
    if (!textExtensions.has(extname(path))) {
      continue;
    }
    const content = await readFile(path, "utf8");
    if (forbiddenMarkers.some((marker) => content.includes(marker))) {
      throw new Error("Production build contains a test Harness marker");
    }
  }
}

const isCommand = process.argv[1] !== undefined && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isCommand) {
  await assertProductionBoundaries(join(frontendRoot, "dist"));
}
