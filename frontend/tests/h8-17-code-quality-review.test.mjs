import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);
const frontendRoot = new URL("frontend/", repositoryRoot);
const scriptsRoot = new URL("scripts/", repositoryRoot);

test("H8-17 keeps the desktop startup gate fail closed at the App boundary", async () => {
  const [app, testEntry] = await Promise.all([
    readFile(new URL("src/app/App.tsx", frontendRoot), "utf8"),
    readFile(new URL("src/test-tauri-main.tsx", frontendRoot), "utf8"),
  ]);

  assert.match(app, /startupCheck:\s*StartupCheck/u);
  assert.doesNotMatch(app, /startupCheck\?:\s*StartupCheck/u);
  assert.doesNotMatch(app, /startupCheck\s*=\s*desktopShellStartupCheck/u);
  assert.doesNotMatch(app, /import\s*\{[^}]*desktopShellStartupCheck/u);
  assert.match(testEntry, /startupCheck=\{desktopShellStartupCheck\}/u);
});

test("H8-17 never converts a failed WDIO App journey into a passing acceptance", async () => {
  const acceptanceRunners = [
    "run_e4_14_acceptance.py",
    "run_b5_13_acceptance.py",
    "run_b5_15_acceptance.py",
    "run_b5_16_acceptance.py",
    "run_h8_16f_acceptance.py",
  ];
  for (const runnerName of acceptanceRunners) {
    const runner = await readFile(new URL(runnerName, scriptsRoot), "utf8");
    assert.doesNotMatch(runner, /graceful_app_exit_observed/u, runnerName);
    assert.match(runner, /returncode\s*!=\s*0/u, runnerName);
  }

  const lifecycleRunner = await readFile(
    new URL("run_e4_14_acceptance.py", scriptsRoot),
    "utf8",
  );
  assert.match(lifecycleRunner, /deadline\s*=\s*time\.monotonic\(\)\s*\+\s*30/u);

  const [nativeEntry, platform] = await Promise.all([
    readFile(new URL("src-tauri/src/lib.rs", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/src/executor_platform.rs", frontendRoot), "utf8"),
  ]);
  assert.match(
    nativeEntry,
    /fn exit_app_for_acceptance[\s\S]*platform\s*\.shutdown_for_app_exit\(\)[\s\S]*map_err\(map_executor_platform_error\)\?/u,
  );
  assert.match(
    platform,
    /pub fn shutdown_for_app_exit\(&self\)\s*->\s*Result<\(\),\s*ExecutorPlatformError>/u,
  );

  const e2eRoot = new URL("e2e-tauri/", frontendRoot);
  const e2eNames = (await readdir(e2eRoot)).filter((name) => name.endsWith(".spec.ts"));
  for (const e2eName of e2eNames) {
    const source = await readFile(new URL(e2eName, e2eRoot), "utf8");
    if (source.includes('core.invoke("exit_app_for_acceptance")')) {
      assert.doesNotMatch(
        source,
        /core\.invoke\("exit_app_for_acceptance"\)[\s\S]{0,200}browser\.pause/u,
        e2eName,
      );
    }
  }
});

test("H8-17 gives every WDIO run one self-cleaning temporary artifact directory", async () => {
  const [lifecycle, nodeProject] = await Promise.all([
    readFile(new URL("wdio-runtime-artifacts.ts", frontendRoot), "utf8"),
    readFile(new URL("tsconfig.node.json", frontendRoot), "utf8"),
  ]);
  assert.match(lifecycle, /mkdtempSync/u);
  assert.match(lifecycle, /tmpdir\(\)/u);
  assert.match(lifecycle, /process\.kill\(parentPid,\s*0\)/u);
  assert.match(lifecycle, /setTimeout\(poll,\s*250\)/u);
  assert.match(lifecycle, /spawn\([\s\S]*process\.execPath/u);
  assert.match(lifecycle, /rmSync/u);
  assert.match(lifecycle, /detached:\s*true/u);
  assert.match(lifecycle, /windowsHide:\s*true/u);
  assert.match(nodeProject, /"wdio\*\.conf\.ts"/u);
  assert.match(nodeProject, /"wdio-runtime-artifacts\.ts"/u);

  const configNames = (await readdir(frontendRoot)).filter((name) =>
    /^wdio(?:\..+)?\.conf\.ts$/u.test(name),
  );
  assert.ok(configNames.length > 0);
  for (const configName of configNames) {
    const source = await readFile(new URL(configName, frontendRoot), "utf8");
    assert.match(source, /wdioRuntimeArtifacts/u, configName);
    assert.match(source, /\.\.\.wdioRuntimeArtifacts/u, configName);
  }
});
