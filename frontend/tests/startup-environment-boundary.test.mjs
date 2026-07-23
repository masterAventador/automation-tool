import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

test("H8-16E composes every startup component through one path-free native aggregate", async () => {
  const [startup, gateway, main, entry, nativeStartup, platform, profiles] = await Promise.all([
    readFile(new URL("src/app/startup.ts", frontendRoot), "utf8"),
    readFile(
      new URL("src/platform/tauri/startup-environment-gateway.ts", frontendRoot),
      "utf8",
    ),
    readFile(new URL("src/main.tsx", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/src/lib.rs", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/src/startup_environment.rs", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/src/executor_platform.rs", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/src/browser_profiles.rs", frontendRoot), "utf8"),
  ]);

  assert.match(startup, /Promise\.allSettled/u);
  assert.match(startup, /control_plane_unavailable/u);
  assert.match(startup, /executor_configuration_required/u);
  assert.match(startup, /browser_component_missing/u);
  assert.match(startup, /browser_component_damaged/u);
  assert.match(startup, /browser_component_version_incompatible/u);
  assert.doesNotMatch(startup, /trusted_browser_selection_required/u);
  assert.match(startup, /app_data_unavailable/u);
  assert.match(gateway, /"check_local_startup_environment"/u);
  assert.match(main, /createDesktopStartupCheck/u);
  assert.match(main, /new TauriStartupEnvironmentGateway\(\)/u);
  assert.match(entry, /fn check_local_startup_environment/u);
  assert.match(entry, /StartupEnvironmentService::initialize/u);
  assert.match(entry, /EmbeddedBrowserAuthority::new/u);
  assert.match(entry, /authority\.resolve\(\)/u);
  assert.doesNotMatch(entry, /BrowserSettingsService::initialize/u);
  assert.match(nativeStartup, /StartupEnvironmentSnapshot/u);
  assert.match(platform, /validate_installed_package/u);
  assert.match(platform, /from_compile_time_configuration/u);
  assert.match(profiles, /revalidate_storage/u);
  assert.doesNotMatch(gateway, /path|directory|profile|token|secret|credential/iu);
  assert.doesNotMatch(nativeStartup, /#\[tauri::command\]/u);
});

test("H8-16E keeps one hidden real-App embedded-browser failure acceptance", async () => {
  const [packageJson, tauriConfig, wdioConfig, spec, orchestrator] = await Promise.all([
    readFile(new URL("package.json", frontendRoot), "utf8"),
    readFile(
      new URL("src-tauri/tauri.startup-environment-e2e.conf.json", frontendRoot),
      "utf8",
    ),
    readFile(new URL("wdio.startup-environment.conf.ts", frontendRoot), "utf8"),
    readFile(new URL("e2e-tauri/startup-environment.spec.ts", frontendRoot), "utf8"),
    readFile(new URL("../scripts/run_h8_16e_acceptance.py", frontendRoot), "utf8"),
  ]);

  assert.match(packageJson, /test:h8-16e-tauri/u);
  assert.match(packageJson, /build:tauri:startup-environment-test/u);
  assert.match(tauriConfig, /"visible"\s*:\s*false/u);
  assert.match(tauriConfig, /com\.aventador\.automationtool\.h816eacceptance/u);
  assert.match(tauriConfig, /build:control-plane-e2e-assets/u);
  assert.match(wdioConfig, /startup-environment\.spec\.ts/u);
  assert.match(spec, /桌面运行环境需要处理/u);
  assert.match(spec, /浏览器组件缺失/u);
  assert.match(spec, /请重新安装官方客户端/u);
  assert.match(spec, /embeddedBrowser/u);
  assert.match(spec, /component_missing/u);
  assert.match(spec, /打开本地修复工具/u);
  assert.match(spec, /重新检查/u);
  assert.match(spec, /check_local_startup_environment/u);
  assert.doesNotMatch(spec, /trustedBrowser|button=保存浏览器选择|RPA 运营工作台/u);
  assert.match(orchestrator, /build_signed_executor/u);
  assert.match(orchestrator, /unused loopback|start_control_plane/iu);
  assert.match(orchestrator, /AUTOMATION_TOOL_ACTION_AUTHORIZATION_PUBLIC_KEY/u);
  assert.match(orchestrator, /AUTOMATION_TOOL_LOCAL_ACTION_MINIMUM_INTERVAL_SECONDS/u);
  assert.match(orchestrator, /AUTOMATION_TOOL_LOCAL_ACTION_TASK_LIMIT/u);
  assert.match(orchestrator, /require_port_closed/u);
  assert.match(orchestrator, /shutil\.rmtree/u);
  assert.doesNotMatch(spec, /exit_app_for_acceptance/u);
  assert.doesNotMatch(orchestrator, /graceful_app_exit_observed/u);
  assert.doesNotMatch(spec, /headless=false|window\.open|https?:\/\//iu);
});
