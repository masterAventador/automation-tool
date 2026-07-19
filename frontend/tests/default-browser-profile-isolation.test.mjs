import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import { extname } from "node:path";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);
const productionRoots = ["backend/src", "frontend/src", "frontend/src-tauri/src"];
const sourceExtensions = new Set([".html", ".js", ".jsx", ".mjs", ".py", ".rs", ".ts", ".tsx"]);
const forbiddenProfileAccess = [
  /Library\/Application Support\/Google\/Chrome/iu,
  /Library\/Application Support\/Microsoft Edge/iu,
  /Google[\\/]Chrome[\\/]User Data/iu,
  /Microsoft[\\/]Edge[\\/]User Data/iu,
  /\.config[\\/](?:google-chrome|microsoft-edge)/iu,
  /--profile-directory/iu,
  /context\.cookies\s*\(/iu,
  /storage_state\s*\(/iu,
  /document\.cookie/iu,
];

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

async function productionSources(directory) {
  const entries = await readdir(new URL(`${directory}/`, repositoryRoot), {
    withFileTypes: true,
  });
  const nested = await Promise.all(
    entries.map(async (entry) => {
      const path = `${directory}/${entry.name}`;
      if (entry.isDirectory()) return productionSources(path);
      if (entry.isFile() && sourceExtensions.has(extname(entry.name))) return [path];
      return [];
    }),
  );
  return nested.flat();
}

test("B5-16 proves the hidden App launches only its private persistent Profile", async () => {
  const [runtime, profiles, appCommands, spec, config, packageJson, runner] =
    await Promise.all([
      readRepositoryFile("backend/src/automation_tool/executor/browser_runtime.py"),
      readRepositoryFile("frontend/src-tauri/src/browser_profiles.rs"),
      readRepositoryFile("frontend/src-tauri/src/lib.rs"),
      readRepositoryFile("frontend/e2e-tauri/default-profile-isolation.spec.ts"),
      readRepositoryFile("frontend/src-tauri/tauri.default-profile-isolation-e2e.conf.json"),
      readRepositoryFile("frontend/package.json"),
      readRepositoryFile("scripts/run_b5_16_acceptance.py"),
    ]);

  assert.match(runtime, /launch_persistent_context\([\s\S]*request\.profile_directory/u);
  assert.match(profiles, /current_douyin_profile/u);
  assert.match(appCommands, /current_douyin_profile\(\)/u);
  assert.match(config, /com\.aventador\.automationtool\.b516acceptance/u);
  assert.match(config, /"visible"\s*:\s*false/u);
  assert.match(packageJson, /test:default-profile-isolation-tauri/u);
  assert.match(spec, /AUTOMATION_TOOL_B516_READY_FILE/u);
  assert.match(spec, /AUTOMATION_TOOL_B516_RELEASE_FILE/u);
  assert.match(spec, /prepare_platform_session_reuse_for_acceptance/u);
  assert.match(runner, /lsof/u);
  assert.match(runner, /--user-data-dir/u);
  assert.match(runner, /browser-profiles/u);
  assert.match(runner, /require_no_residual_project_processes/u);
  assert.doesNotMatch(spec, /profileDirectory|profileId|cookie/iu);

  const sources = (await Promise.all(productionRoots.map(productionSources))).flat();
  for (const path of sources) {
    const source = await readRepositoryFile(path);
    for (const pattern of forbiddenProfileAccess) {
      assert.doesNotMatch(source, pattern, `${path} must not access a default browser Profile`);
    }
  }
});
