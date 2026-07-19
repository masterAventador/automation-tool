import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

async function openSettings(): Promise<void> {
  await browser
    .$("//li[contains(@class,'ant-menu-item') and .//*[normalize-space()='设置与诊断']]")
    .click();
  await expect(await browser.$("h2")).toHaveText("设置与诊断");
  await expect(await browser.$(".browser-settings-card")).toBeDisplayed();
}

describe("B5-04 hidden App browser settings acceptance", () => {
  it("selects and reloads a trusted browser without exposing a path", async () => {
    await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");
    await openSettings();

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

    const initialText = await browser.$("body").getText();
    assert.doesNotMatch(
      initialText,
      /\/Applications|Contents\/MacOS|Program Files|chrome\.exe|msedge\.exe|[A-Z]:\\/i,
    );

    await browser.refresh();
    await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");
    await openSettings();
    await expect(await browser.$(".browser-settings-card")).toHaveText(
      expect.stringContaining(`当前选择：${selectedLabel}`),
    );
    assert.doesNotMatch(
      await browser.$("body").getText(),
      /\/Applications|Contents\/MacOS|Program Files|chrome\.exe|msedge\.exe|[A-Z]:\\/i,
    );
  });
});
