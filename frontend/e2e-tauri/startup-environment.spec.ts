import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

interface StartupEnvironmentSnapshot {
  readonly appData: "ready" | "unavailable";
  readonly executor: "ready" | "configuration_required" | "unavailable";
  readonly trustedBrowser: "ready" | "selection_required" | "unavailable";
}

interface ExecutorStatusSnapshot {
  readonly state: string;
}

const PRIVATE_VALUE_PATTERN =
  /\/Applications|Contents\/MacOS|Program Files|chrome\.exe|msedge\.exe|[A-Z]:\\|authorization|token|secret|credential/i;

describe("H8-16E hidden App startup environment acceptance", () => {
  it("repairs the trusted browser selection and reaches the workbench", async () => {
    await expect(await browser.$("h2")).toHaveText("桌面运行环境需要处理");
    const blockedText = await browser.$("body").getText();
    // EB-08/EB-10：开发构建资源目录无内置发行物时按组件语义提示。
    assert.match(blockedText, /浏览器组件缺失|浏览器组件损坏/);
    assert.doesNotMatch(blockedText, PRIVATE_VALUE_PATTERN);

    await browser.$("button=打开本地修复工具").click();
    const firstChoice = await browser.$(
      ".browser-settings-card label.ant-radio-wrapper",
    );
    await expect(firstChoice).toBeDisplayed();
    const selectedLabel = (await firstChoice.getText()).trim();
    assert.match(selectedLabel, /^(Google Chrome|Microsoft Edge)$/);
    await firstChoice.click();
    await browser.$("button=保存浏览器选择").click();
    await expect(await browser.$(".browser-settings-card")).toHaveText(
      expect.stringContaining(`当前选择：${selectedLabel}`),
    );

    await browser.$("button=重新检查").click();
    await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");
    const snapshot = (await browser.tauri.execute(({ core }) =>
      core.invoke("check_local_startup_environment"),
    )) as StartupEnvironmentSnapshot;
    assert.deepEqual(snapshot, {
      appData: "ready",
      executor: "ready",
      trustedBrowser: "ready",
    });
    const executor = (await browser.tauri.execute(({ core }) =>
      core.invoke("get_executor_status"),
    )) as ExecutorStatusSnapshot;
    assert.equal(executor.state, "stopped");
    assert.doesNotMatch(await browser.$("body").getText(), PRIVATE_VALUE_PATTERN);
  });
});
