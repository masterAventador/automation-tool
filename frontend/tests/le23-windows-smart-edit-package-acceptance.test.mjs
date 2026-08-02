import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

test("LE-23 drives the installed Windows NSIS through the formal smart-edit UI", async () => {
  const [spec, runner, wdio, configText, packageText, gitignore] = await Promise.all([
    readFile(new URL("e2e-tauri/smart-edit-package.spec.ts", frontendRoot), "utf8"),
    readFile(
      new URL("../scripts/run_le_23_windows_package_acceptance.py", frontendRoot),
      "utf8",
    ),
    readFile(new URL("wdio.le23-windows-package.conf.ts", frontendRoot), "utf8"),
    readFile(
      new URL("src-tauri/tauri.le23-windows-package-e2e.conf.json", frontendRoot),
      "utf8",
    ),
    readFile(new URL("package.json", frontendRoot), "utf8"),
    readFile(new URL("../.gitignore", frontendRoot), "utf8"),
  ]);
  const config = JSON.parse(configText);
  const scripts = JSON.parse(packageText).scripts;

  assert.match(spec, /AUTOMATION_TOOL_LE22_MODEL_KEY/u);
  assert.equal(
    scripts["test:le-23-windows-package"],
    "uv run --project ../backend --locked python ../scripts/run_le_23_windows_package_acceptance.py",
  );
  assert.equal(config.identifier, "com.aventador.automationtool.le23windowspackage");
  assert.equal(config.productName, "Automation Tool LE23 Windows Package Acceptance");
  assert.equal(config.mainBinaryName, "automation-tool-le23-windows-package");
  assert.equal(config.app.windows.length, 1);
  assert.equal(config.app.windows[0].visible, false);
  assert.deepEqual(config.bundle.targets, ["nsis"]);
  assert.equal(config.bundle.windows.nsis.installMode, "currentUser");
  assert.match(wdio, /LE23_WINDOWS_APP_BINARY/u);
  assert.match(wdio, /smart-edit-package\.spec\.ts/u);
  assert.match(wdio, /driverProvider:\s*"embedded"/u);

  for (const required of [
    "require_windows",
    "require_non_elevated_process",
    "build_executor_candidate",
    "pnpm_executable",
    "compose_command",
    "write_windows_release_configuration",
    "stage_browser_distribution",
    "install_video_runtime",
    "install_motion_catalog",
    "install_and_seal",
    "require_packaged_browser",
    "require_packaged_video_runtime",
    "require_packaged_motion_catalog",
    "audit_material_video_worker_candidate",
    "audit_installed_motion_catalog",
    "prepare_voice_fixture",
    "collect_database_evidence",
    "collect_editing_job_failure",
    "validate_le22_ffprobe",
    "compare_pcm_envelopes",
    "install_package",
    "uninstall_and_check",
    "ffmpeg.exe",
    "ffprobe.exe",
    "cjkFont",
    "AUTOMATION_TOOL_LE22_MODEL_KEY",
  ]) {
    assert.ok(runner.includes(required), `LE-23 Windows runner is missing ${required}`);
  }
  const installFunction = runner.slice(
    runner.indexOf("def install_package"),
    runner.indexOf("def uninstall_and_check"),
  );
  assert.match(
    installFunction.slice(0, installFunction.indexOf("run_checked")),
    /machine_wide=True/u,
  );
  assert.match(installFunction, /if machine_wide:/u);
  assert.doesNotMatch(runner, /installed_binary\(root\)\.is_file\(\)/u);
  assert.doesNotMatch(runner, /__import__\(|shutil\.which\("pnpm/u);
  assert.doesNotMatch(runner, /mock|stub|sessionStorage|localStorage/iu);
  assert.match(
    runner,
    /assert_no_private_evidence\(output, api_key, source\)\n\s+print\(output, end=""\)/u,
  );
  assert.match(runner, /failureCode=\{failure_code\}/u);
  assert.match(gitignore, /^frontend\/dist-le23-windows\/$/mu);
});
