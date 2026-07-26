import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);
const frontendRoot = new URL("frontend/", repositoryRoot);

test("H8-21 routes decisions and cached packages through one Rust-only install coordinator", async () => {
  const [nativeEntry, coordinator, installation, cache, packageManifest, capability] =
    await Promise.all([
      readFile(new URL("src-tauri/src/lib.rs", frontendRoot), "utf8"),
      readFile(new URL("src-tauri/src/app_update_coordinator.rs", frontendRoot), "utf8"),
      readFile(new URL("src-tauri/src/app_update_installation.rs", frontendRoot), "utf8"),
      readFile(new URL("src-tauri/src/app_update_cache.rs", frontendRoot), "utf8"),
      readFile(new URL("package.json", frontendRoot), "utf8"),
      readFile(new URL("src-tauri/capabilities/main.json", frontendRoot), "utf8"),
    ]);

  assert.match(nativeEntry, /fn decide_app_update/u);
  assert.match(coordinator, /UpdateDecision::|pub fn decide/u);
  assert.match(coordinator, /UpdateCheckTrigger::Startup/u);
  assert.match(coordinator, /UpdatePolicyAction::Forced/u);
  assert.match(coordinator, /UpdatePolicyAction::InstallRequested/u);
  const probeOffset = coordinator.indexOf("struct UpdateInstallProbe");
  assert.notEqual(probeOffset, -1);
  const probeDefinition = coordinator.slice(Math.max(0, probeOffset - 220), probeOffset + 40);
  assert.match(probeDefinition, /debug_assertions/u);
  assert.match(probeDefinition, /feature = "desktop-e2e"/u);
  assert.match(probeDefinition, /not\(feature = "control-plane-e2e"\)/u);
  assert.match(installation, /OfficialUpdatePackageInstaller/u);
  assert.match(installation, /shutdown_for_app_exit/u);
  assert.match(installation, /self\.app\.restart\(\)/u);
  assert.match(cache, /read_verified_package/u);
  assert.match(cache, /\.verify\(&package, &signature, false\)/u);
  assert.match(packageManifest, /test:h8-21-app/u);
  assert.doesNotMatch(capability, /updater:/u);
  assert.doesNotMatch(packageManifest, /@tauri-apps\/plugin-updater/u);
});

test("H8-21 acceptance stays hidden and decides through the production update UI", async () => {
  const [tauriConfig, spec, runner] = await Promise.all([
    readFile(
      new URL("src-tauri/tauri.update-installation-e2e.conf.json", frontendRoot),
      "utf8",
    ),
    // The zero-click surrogate that used to live here was retired: it drove the
    // same runner, build and scenarios as `update-ui.spec.ts` but reached the
    // decisions over IPC, which CLAUDE.md §8 does not accept as acceptance.
    readFile(new URL("e2e-tauri/update-ui.spec.ts", frontendRoot), "utf8"),
    readFile(new URL("scripts/run_h8_21_acceptance.py", repositoryRoot), "utf8"),
  ]);
  const configuration = JSON.parse(tauriConfig);

  assert.equal(configuration.identifier, "com.aventador.automationtool.h821acceptance");
  assert.equal(configuration.app.windows[0].visible, false);
  assert.equal(configuration.build.frontendDist, "../dist-h821");
  assert.match(spec, /button=立即安装/u);
  assert.match(spec, /installation_launched/u);
  assert.match(runner, /create_app\(database=None, desktop_update_catalog=/u);
  assert.match(runner, /AUTOMATION_TOOL_UPDATE_INSTALL_PROBE/u);
  assert.match(runner, /ACCEPTANCE_ASSETS/u);
  assert.match(runner, /forced-reopen/u);
});
