import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

test("H8-22 keeps update UI on fixed Rust commands without updater JavaScript capability", async () => {
  const [main, center, gateway, manifest, capability] = await Promise.all([
    readFile(new URL("src/main.tsx", frontendRoot), "utf8"),
    readFile(new URL("src/features/app-updates/AppUpdateCenter.tsx", frontendRoot), "utf8"),
    readFile(new URL("src/platform/tauri/app-update-gateway.ts", frontendRoot), "utf8"),
    readFile(new URL("package.json", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/capabilities/main.json", frontendRoot), "utf8"),
  ]);

  assert.match(main, /new TauriAppUpdateGateway\(\)/u);
  assert.match(main, /appUpdateGateway=/u);
  assert.match(center, /install_now/u);
  assert.match(center, /skip_version/u);
  assert.match(center, /forced/u);
  assert.match(gateway, /get_app_update_state/u);
  assert.match(gateway, /check_app_update_now/u);
  assert.match(gateway, /decide_app_update/u);
  assert.doesNotMatch(manifest, /@tauri-apps\/plugin-updater/iu);
  assert.doesNotMatch(capability, /updater:/iu);
});

test("H8-22 owns a hidden original-App UI acceptance without claiming platform signing", async () => {
  const [spec, config, runner] = await Promise.all([
    readFile(new URL("e2e-tauri/update-ui.spec.ts", frontendRoot), "utf8"),
    readFile(new URL("wdio.update-ui.conf.ts", frontendRoot), "utf8"),
    readFile(new URL("../scripts/run_h8_22_acceptance.py", frontendRoot), "utf8"),
  ]);

  assert.match(spec, /button=稍后提醒/u);
  assert.match(spec, /button=跳过此版本/u);
  assert.match(spec, /button=立即安装/u);
  assert.match(config, /update-ui\.spec\.ts/u);
  assert.match(runner, /wdio\.update-ui\.conf\.ts/u);
  assert.doesNotMatch(runner, /Developer ID|signtool|notarytool/iu);
});
