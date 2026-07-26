import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);
const frontendRoot = new URL("frontend/", repositoryRoot);

test("H8-20 wires one Rust-only startup, periodic and manual update entrypoint", async () => {
  const [nativeEntry, coordinator, buildScript, packageManifest, capability] = await Promise.all([
    readFile(new URL("src-tauri/src/lib.rs", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/src/app_update_coordinator.rs", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/build.rs", frontendRoot), "utf8"),
    readFile(new URL("package.json", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/capabilities/main.json", frontendRoot), "utf8"),
  ]);

  assert.match(nativeEntry, /tauri_plugin_updater::Builder::new\(\)\.build\(\)/u);
  assert.match(nativeEntry, /coordinator\.start_background\(\)/u);
  assert.match(nativeEntry, /fn get_app_update_state/u);
  assert.match(nativeEntry, /fn check_app_update_now/u);
  assert.match(coordinator, /UpdateCheckTrigger::Startup/u);
  assert.match(coordinator, /UpdateCheckTrigger::Periodic/u);
  assert.match(nativeEntry, /UpdateCheckTrigger::Manual/u);
  assert.match(coordinator, /DEFAULT_UPDATE_POLL_INTERVAL/u);
  assert.match(buildScript, /release update configuration is required/u);
  assert.match(buildScript, /release update configuration is invalid/u);
  assert.match(buildScript, /PublicKey::decode/u);
  assert.match(packageManifest, /test:h8-20-app/u);
  assert.doesNotMatch(capability, /updater:/u);
  assert.doesNotMatch(packageManifest, /@tauri-apps\/plugin-updater/u);
});

test("H8-20 keeps resumable verified cache transport-private and business agnostic", async () => {
  const cache = await readFile(
    new URL("src-tauri/src/app_update_cache.rs", frontendRoot),
    "utf8",
  );
  assert.match(cache, /RANGE/u);
  assert.match(cache, /IF_RANGE/u);
  assert.match(cache, /CONTENT_RANGE/u);
  assert.match(cache, /verify_stream/u);
  assert.match(cache, /atomic_replace/u);
  assert.match(cache, /AppDataSecretStore/u);
  assert.match(cache, /candidate\.partial/u);
  assert.match(cache, /candidate\.package/u);
  const storedRecords = cache.slice(
    cache.indexOf("pub struct CachedUpdateRecord"),
    cache.indexOf("pub struct AppUpdateCache"),
  );
  assert.doesNotMatch(storedRecords, /url\s*:|signature\s*:/iu);
  const production = cache.split("#[cfg(test)]", 1)[0];
  for (const forbidden of ["douyin", "xiaohongshu", "taskId", "customerId"]) {
    assert.doesNotMatch(production, new RegExp(forbidden, "iu"));
  }
});

test("release update configuration rejects reserved placeholder feed hosts", async () => {
  const [
    buildScript,
    coordinator,
    tauriConfigSource,
    macosCandidateRunner,
    windowsCandidateRunner,
    releaseBuilder,
  ] = await Promise.all([
    readFile(new URL("src-tauri/build.rs", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/src/app_update_coordinator.rs", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/tauri.conf.json", frontendRoot), "utf8"),
    readFile(new URL("../scripts/run_p9_03_acceptance.py", frontendRoot), "utf8"),
    readFile(new URL("../scripts/run_p9_04_acceptance.py", frontendRoot), "utf8"),
    readFile(new URL("../scripts/build_release_package.py", frontendRoot), "utf8"),
  ]);
  const tauriConfig = JSON.parse(tauriConfigSource);

  assert.deepEqual(tauriConfig.plugins.updater.endpoints, []);
  assert.equal(tauriConfig.plugins.updater.pubkey, "");
  assert.doesNotMatch(tauriConfigSource, /\.invalid/iu);
  for (const source of [buildScript, coordinator]) {
    for (const reservedSuffix of [".invalid", ".test", ".example", ".localhost"]) {
      assert.match(source, new RegExp(`ends_with\\("${reservedSuffix.replace(".", "\\.")}"\\)`));
    }
    for (const reservedExampleSuffix of [".example.com", ".example.net", ".example.org"]) {
      assert.match(
        source,
        new RegExp(`ends_with\\("${reservedExampleSuffix.replace(".", "\\.")}"\\)`),
      );
    }
    assert.match(source, /trim_end_matches\('\.'\)/u);
    for (const reservedHost of [
      "invalid",
      "test",
      "example",
      "localhost",
      "example.com",
      "example.net",
      "example.org",
    ]) {
      assert.match(source, new RegExp(`host == "${reservedHost.replace(".", "\\.")}"`));
    }
  }
  assert.match(buildScript, /AUTOMATION_TOOL_UPDATE_DISABLED/u);
  for (const candidateRunner of [macosCandidateRunner, windowsCandidateRunner]) {
    assert.match(
      candidateRunner,
      /AUTOMATION_TOOL_UPDATE_DISABLED["']?\]?\s*(?::|=)\s*["']1["']/u,
    );
    assert.doesNotMatch(candidateRunner, /updates\.candidate\.invalid/iu);
  }
  assert.match(releaseBuilder, /--update-endpoint/u);
  assert.match(releaseBuilder, /--update-public-key-file/u);
  assert.match(releaseBuilder, /update_endpoint=update_endpoint/u);
  assert.match(releaseBuilder, /update_public_key=update_public_key/u);
});

test("H8-20 validates a real hidden App against the production FastAPI feed", async () => {
  const [tauriConfig, wdioConfig, spec, runner, backendRoute] = await Promise.all([
    readFile(new URL("src-tauri/tauri.update-download-e2e.conf.json", frontendRoot), "utf8"),
    readFile(new URL("wdio.update-download.conf.ts", frontendRoot), "utf8"),
    readFile(new URL("e2e-tauri/update-download.spec.ts", frontendRoot), "utf8"),
    readFile(new URL("scripts/run_h8_20_acceptance.py", repositoryRoot), "utf8"),
    readFile(
      new URL("backend/src/automation_tool/control_plane/api/desktop_updates.py", repositoryRoot),
      "utf8",
    ),
  ]);
  const configuration = JSON.parse(tauriConfig);
  assert.equal(configuration.identifier, "com.aventador.automationtool.h820acceptance");
  assert.equal(configuration.app.windows[0].visible, false);
  assert.match(wdioConfig, /update-download\.spec\.ts/u);
  // The manual re-check is reached the way a user reaches it, from the button in
  // 设置与诊断; invoking `check_app_update_now` from the spec proved the command
  // worked, not that anybody could get to it.
  assert.match(spec, /button=检查更新/u);
  assert.match(spec, /transport_unavailable/u);
  assert.match(runner, /create_app\(database=None, desktop_update_catalog=catalog\)/u);
  assert.match(runner, /bytes=2-/u);
  assert.match(runner, /verify_private_cache/u);
  assert.match(backendRoute, /desktop_update_feed/u);
});
