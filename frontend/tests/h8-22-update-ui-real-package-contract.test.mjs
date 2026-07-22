import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);
const frontendRoot = new URL("frontend/", repositoryRoot);

test("H8-22 keeps the update UI business-agnostic and behind fixed native commands", async () => {
  const [ui, gateway, nativeEntry, packageManifest, capability] = await Promise.all([
    readFile(new URL("src/features/app-updates/AppUpdates.tsx", frontendRoot), "utf8"),
    readFile(new URL("src/platform/tauri/app-update-gateway.ts", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/src/lib.rs", frontendRoot), "utf8"),
    readFile(new URL("package.json", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/capabilities/main.json", frontendRoot), "utf8"),
  ]);

  for (const command of ["get_app_update_state", "check_app_update_now", "decide_app_update"]) {
    assert.ok(gateway.includes(`"${command}"`));
    assert.match(nativeEntry, new RegExp(`fn ${command}`, "u"));
  }
  for (const label of ["检查更新", "立即安装", "暂不安装", "跳过此版本"]) {
    assert.match(ui, new RegExp(label, "u"));
  }
  for (const forbidden of ["douyin", "xiaohongshu", "taskId", "customerId"]) {
    assert.doesNotMatch(ui, new RegExp(forbidden, "iu"));
    assert.doesNotMatch(gateway, new RegExp(forbidden, "iu"));
  }
  assert.doesNotMatch(capability, /updater:/u);
  assert.doesNotMatch(packageManifest, /@tauri-apps\/plugin-updater/u);
});

test("H8-22 real acceptance uses hidden isolated signed packages on macOS and Windows", async () => {
  const [configText, wdioConfig, spec, runner, workflow, packageManifest] = await Promise.all([
    readFile(new URL("src-tauri/tauri.real-update-e2e.conf.json", frontendRoot), "utf8"),
    readFile(new URL("wdio.real-update.conf.ts", frontendRoot), "utf8"),
    readFile(new URL("e2e-tauri/real-update.spec.ts", frontendRoot), "utf8"),
    readFile(new URL("scripts/run_h8_22_real_update_acceptance.py", repositoryRoot), "utf8"),
    readFile(new URL(".github/workflows/desktop.yml", repositoryRoot), "utf8"),
    readFile(new URL("package.json", frontendRoot), "utf8"),
  ]);
  const configuration = JSON.parse(configText);

  assert.equal(configuration.identifier, "com.aventador.automationtool.h822realacceptance");
  assert.equal(configuration.app.windows[0].visible, false);
  assert.equal(configuration.build.frontendDist, "../dist-h822-real");
  assert.equal(configuration.bundle.createUpdaterArtifacts, true);
  assert.equal(configuration.bundle.windows.nsis.installMode, "currentUser");
  assert.match(wdioConfig, /H822_REAL_APP_BINARY/u);
  assert.match(spec, /H822_REAL_SCENARIO/u);
  assert.match(spec, /检查更新/u);
  assert.match(runner, /TAURI_SIGNING_PRIVATE_KEY/u);
  assert.match(runner, /create_app/u);
  assert.match(runner, /OPTIONAL_VERSION/u);
  assert.match(runner, /NEW_VERSION/u);
  assert.match(runner, /sys\.platform == "darwin"/u);
  assert.match(runner, /sys\.platform == "win32"/u);
  assert.match(runner, /uninstall_windows/u);
  assert.match(runner, /canonical_temporary_directory/u);
  assert.match(
    runner,
    /environment\.pop\("AUTOMATION_TOOL_UPDATE_INSTALL_PROBE", None\)/u,
  );
  assert.doesNotMatch(runner, /AUTOMATION_TOOL_UPDATE_INSTALL_PROBE"\] =/u);
  assert.match(packageManifest, /uv run --project \.\.\/backend --locked python/u);
  assert.match(workflow, /h8-22-real-update:/u);
  assert.match(workflow, /runner: \[macos-latest, windows-latest\]/u);
  assert.match(workflow, /pnpm test:h8-22-real-update/u);
});
