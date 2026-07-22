import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

async function waitForBodyText(expected: string): Promise<void> {
  const body = await browser.$("body");
  try {
    await browser.waitUntil(async () => (await body.getText()).includes(expected), {
      interval: 100,
      timeout: 60_000,
      timeoutMsg: `real updater App did not render: ${expected}`,
    });
  } catch {
    const state = (await browser.tauri.execute(({ core }) =>
      core.invoke("get_app_update_state"),
    )) as { readonly state?: string; readonly stage?: string; readonly code?: string };
    throw new Error(
      `real updater App state: ${state.state ?? "missing"}/${state.stage ?? "none"}/${state.code ?? "none"}`,
    );
  }
}

async function clickButton(label: string): Promise<void> {
  const button = await browser.$(`button=${label}`);
  await button.waitForExist({ timeout: 60_000 });
  await browser.waitUntil(async () => button.isEnabled(), {
    interval: 100,
    timeout: 60_000,
    timeoutMsg: `${label} remained disabled`,
  });
  await button.click();
}

async function holdRunnerForInstaller(): Promise<void> {
  await new Promise<void>((resolve) => {
    globalThis.setTimeout(resolve, 20_000);
  });
}

describe("H8-22 real signed package update acceptance", () => {
  it("starts every package transition from the hidden product App UI", async () => {
    await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");
    assert.deepEqual(await browser.tauri.listWindows(), ["main"]);
    const scenario = process.env.H822_REAL_SCENARIO;

    if (scenario === "optional") {
      await waitForBodyText("发现新版本 0.2.0");
      await clickButton("跳过此版本");

      const settingsMenu = await browser.$(
        "//li[contains(@class,'ant-menu-item') and .//*[normalize-space()='设置与诊断']]",
      );
      await settingsMenu.click();
      await expect(await browser.$("h2")).toHaveText("设置与诊断");
      await waitForBodyText("版本 0.2.0 已跳过");

      await clickButton("检查更新");
      await waitForBodyText("版本 0.2.0 已跳过，不再提示");
      await clickButton("检查更新");
      await waitForBodyText("发现新版本 0.3.0");
      await clickButton("立即安装");
      await holdRunnerForInstaller();
      return;
    }

    if (scenario === "forced-first") {
      await waitForBodyText("必须安装的更新已准备好");
      for (const label of ["暂不安装", "跳过此版本"]) {
        const button = await browser.$(`button=${label}`);
        assert.equal((await button.isExisting()) && (await button.isDisplayed()), false);
      }
      return;
    }

    if (scenario === "forced-reopen") {
      await holdRunnerForInstaller();
      return;
    }

    throw new Error("H822_REAL_SCENARIO is invalid");
  });
});
