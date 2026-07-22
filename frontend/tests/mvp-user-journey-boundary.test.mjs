import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);
const frontendRoot = new URL("frontend/", repositoryRoot);

test("H8-16F automatically hands a newly waiting Task to the existing login entry", async () => {
  const [details, shell, sessions] = await Promise.all([
    readFile(new URL("src/features/task-runs/TaskRunDetails.tsx", frontendRoot), "utf8"),
    readFile(new URL("src/app/WorkbenchShell.tsx", frontendRoot), "utf8"),
    readFile(
      new URL("src/features/platform-sessions/PlatformSessions.tsx", frontendRoot),
      "utf8",
    ),
  ]);

  assert.match(details, /previous !== "awaiting_platform_login"/u);
  assert.match(details, /status === "awaiting_platform_login"/u);
  assert.match(details, /onPlatformLoginRequired\(\)/u);
  assert.match(shell, /onPlatformLoginRequired=\{\(\) => openPlatformPage\(true\)\}/u);
  assert.match(shell, /autoOpenLogin=\{autoOpenPlatformLogin\}/u);
  assert.match(sessions, /void Promise\.resolve\(\)\.then\(\(\) => run\("open"\)\)/u);
});

test("H8-16F keeps one hidden original-caller MVP journey with controlled browser facts", async () => {
  const [packageJson, configSource, wdio, journey, runner, executorFixture, executorSpec] =
    await Promise.all([
      readFile(new URL("package.json", frontendRoot), "utf8"),
      readFile(
        new URL("src-tauri/tauri.mvp-user-journey-e2e.conf.json", frontendRoot),
        "utf8",
      ),
      readFile(new URL("wdio.mvp-user-journey.conf.ts", frontendRoot), "utf8"),
      readFile(new URL("e2e-tauri/mvp-user-journey.spec.ts", frontendRoot), "utf8"),
      readFile(new URL("scripts/run_h8_16f_acceptance.py", repositoryRoot), "utf8"),
      readFile(
        new URL("backend/tests/fixtures/h8_16f_executor.py", repositoryRoot),
        "utf8",
      ),
      readFile(
        new URL(
          "backend/tests/fixtures/automation-tool-executor-h816f.spec",
          repositoryRoot,
        ),
        "utf8",
      ),
    ]);
  const config = JSON.parse(configSource);

  assert.match(packageJson, /test:h8-16f-tauri/u);
  assert.match(packageJson, /build:tauri:mvp-user-journey-test/u);
  assert.equal(config.identifier, "com.aventador.automationtool.h816facceptance");
  assert.deepEqual(config.app.windows, [
    { label: "main", title: "自动化运营工具", visible: false },
  ]);
  assert.match(wdio, /mvp-user-journey\.spec\.ts/u);
  for (const userAction of [
    "创建任务",
    "查看运行详情",
    "开始目标发现",
    "登录正常",
    "选择目标 验收目标 2",
    "确认执行",
    "确认目标",
    "目标主页已确认可见",
    "任务已进入终态",
  ]) {
    assert.match(journey, new RegExp(userAction, "u"));
  }
  assert.match(journey, /prepare_task_create_form_for_acceptance/u);
  assert.doesNotMatch(
    journey,
    /core\.invoke\("(?:start_task_discovery|confirm_task_target_preview|get_task_target_results|restart_executor|select_browser)"/u,
  );
  assert.match(journey, /core\.invoke\("exit_app_for_acceptance"\)/u);
  assert.doesNotMatch(journey, /headless=false/iu);

  assert.match(runner, /build_signed_executor/u);
  assert.match(runner, /automation-tool-executor-h816f\.spec/u);
  assert.match(runner, /AUTOMATION_TOOL_ACTION_AUTHORIZATION_PRIVATE_KEY/u);
  assert.match(runner, /AUTOMATION_TOOL_ACTION_AUTHORIZATION_PUBLIC_KEY/u);
  assert.match(runner, /compose_command/u);
  assert.match(runner, /unused_loopback_port/u);
  assert.match(runner, /require_port_closed/u);
  assert.match(runner, /shutil\.rmtree/u);
  assert.doesNotMatch(runner, /graceful_app_exit_observed/u);
  assert.match(runner, /public_observation_counts/u);
  assert.match(runner, /public_executor_facts/u);
  assert.match(journey, /waiting-platform-login handoff facts/u);

  assert.match(executorFixture, /ProductionDouyinActionOperation/u);
  assert.match(executorFixture, /DouyinBrowseExecution/u);
  assert.match(executorFixture, /https:\/\/www\.douyin\.com\/user\//u);
  assert.match(executorFixture, /request\.headless is not True/u);
  assert.match(executorFixture, /DouyinDiscoveryOperationState\.LOGIN_REQUIRED/u);
  assert.match(executorFixture, /DouyinDiscoveryOperationState\.COMPLETED/u);
  assert.match(executorSpec, /h8_16f_executor\.py/u);
});
