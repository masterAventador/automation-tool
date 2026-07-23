import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

interface StartupEnvironmentSnapshot {
  readonly appData: "ready" | "unavailable";
  readonly executor: "ready" | "configuration_required" | "unavailable";
  readonly embeddedBrowser:
    | "ready"
    | "component_missing"
    | "component_damaged"
    | "version_incompatible";
}

interface ExecutorStatusSnapshot {
  readonly state: string;
}

const PRIVATE_VALUE_PATTERN =
  /\/Applications|Contents\/MacOS|Program Files|chrome\.exe|msedge\.exe|[A-Z]:\\|authorization|token|secret|credential/i;
const SYSTEM_BROWSER_CHOICE_PATTERN =
  /Google Chrome|Microsoft Edge|保存浏览器选择|当前选择/;

describe("H8-16E hidden App startup environment acceptance", () => {
  it("keeps the workbench blocked when the embedded browser is missing", async () => {
    await expect(await browser.$("h2")).toHaveText("桌面运行环境需要处理");
    const blockedText = await browser.$("body").getText();
    assert.match(blockedText, /浏览器组件缺失/);
    assert.match(
      blockedText,
      /请重新安装官方客户端；无需也不要单独安装其他浏览器/,
    );
    assert.doesNotMatch(blockedText, PRIVATE_VALUE_PATTERN);
    assert.doesNotMatch(blockedText, SYSTEM_BROWSER_CHOICE_PATTERN);

    const snapshot = (await browser.tauri.execute(({ core }) =>
      core.invoke("check_local_startup_environment"),
    )) as StartupEnvironmentSnapshot;
    assert.deepEqual(snapshot, {
      appData: "ready",
      executor: "ready",
      embeddedBrowser: "component_missing",
    });

    await browser.$("button=打开本地修复工具").click();
    await expect(await browser.$(".diagnostics-status-card")).toHaveText(
      expect.stringContaining("本地执行器已停止"),
    );
    assert.equal(await browser.$(".browser-settings-card").isExisting(), false);
    assert.doesNotMatch(
      await browser.$("body").getText(),
      SYSTEM_BROWSER_CHOICE_PATTERN,
    );

    await browser.$("button=重新检查").click();
    await expect(await browser.$("h2")).toHaveText("桌面运行环境需要处理");
    assert.match(await browser.$("body").getText(), /浏览器组件缺失/);

    const executor = (await browser.tauri.execute(({ core }) =>
      core.invoke("get_executor_status"),
    )) as ExecutorStatusSnapshot;
    assert.equal(executor.state, "stopped");
    assert.doesNotMatch(await browser.$("body").getText(), PRIVATE_VALUE_PATTERN);
  });
});
