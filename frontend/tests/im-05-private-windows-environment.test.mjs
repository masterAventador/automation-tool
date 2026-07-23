import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

test("IM-05 gives Windows WebUI dependencies only a task-private home", async () => {
  const orchestrator = await readFile(
    new URL(
      "frontend/src-tauri/src/local_video_orchestrator.rs",
      repositoryRoot,
    ),
    "utf8",
  );
  const isolatedEnvironment = orchestrator.match(
    /if launch\.isolated_environment \{([\s\S]*?)\n    \}/u,
  )?.[1];
  assert.ok(isolatedEnvironment, "missing isolated Worker environment");

  assert.match(isolatedEnvironment, /command\.env_clear\(\)/u);
  for (const variable of [
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "TEMP",
    "TMP",
  ]) {
    assert.match(isolatedEnvironment, new RegExp(`"${variable}"`, "u"));
  }
  assert.match(
    isolatedEnvironment,
    /command\.env\(name, &launch\.asset_root\)/u,
  );
  assert.doesNotMatch(
    isolatedEnvironment,
    /std::env::var_os\("(?:HOME|USERPROFILE|APPDATA|LOCALAPPDATA|TEMP|TMP)"\)/u,
  );
});
