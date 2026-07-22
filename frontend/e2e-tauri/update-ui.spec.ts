import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

async function waitForBodyText(expected: string): Promise<void> {
  const body = await browser.$("body");
  await browser.waitUntil(async () => (await body.getText()).includes(expected), {
    interval: 100,
    timeout: 30_000,
    timeoutMsg: `update UI did not render: ${expected}`,
  });
}

async function clickVisibleButton(label: string): Promise<void> {
  const button = await browser.$(`button=${label}`);
  await button.waitForExist({ timeout: 30_000 });
  await browser.waitUntil(async () => button.isEnabled(), {
    interval: 100,
    timeout: 30_000,
    timeoutMsg: `${label} remained disabled`,
  });
  await button.click();
}

describe("H8-22 hidden App update UI acceptance", () => {
  it("uses only the product settings, prompt and decision controls", async () => {
    await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");
    assert.deepEqual(await browser.tauri.listWindows(), ["main"]);
    const scenario = process.env.H822_UI_SCENARIO;

    if (scenario === "optional") {
      await waitForBodyText("发现新版本 0.2.0");
      await clickVisibleButton("暂不安装");

      const settingsMenu = await browser.$(
        "//li[contains(@class,'ant-menu-item') and .//*[normalize-space()='设置与诊断']]",
      );
      await settingsMenu.click();
      await expect(await browser.$("h2")).toHaveText("设置与诊断");
      await waitForBodyText("版本 0.2.0 已暂缓安装");

      await clickVisibleButton("检查更新");
      await waitForBodyText("发现新版本 0.2.0");
      await clickVisibleButton("跳过此版本");
      await waitForBodyText("版本 0.2.0 已跳过");

      await clickVisibleButton("检查更新");
      await waitForBodyText("版本 0.2.0 已跳过，不再提示");

      await clickVisibleButton("检查更新");
      await waitForBodyText("发现新版本 0.3.0");
      await clickVisibleButton("立即安装");
      await waitForBodyText("正在进入安装流程");
      return;
    }

    if (scenario === "forced") {
      await waitForBodyText("必须安装的更新已准备好");
      await waitForBodyText("将在下次启动 App 时自动安装。");
      for (const label of ["暂不安装", "跳过此版本", "立即安装"]) {
        const button = await browser.$(`button=${label}`);
        assert.equal(
          (await button.isExisting()) && (await button.isDisplayed()),
          false,
          `${label} must not be offered for forced`,
        );
      }
      return;
    }

    throw new Error("H822_UI_SCENARIO is invalid");
  });
});
