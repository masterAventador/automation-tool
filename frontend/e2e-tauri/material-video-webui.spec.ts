import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

const TEST_KEY = "sk-im05-invalid-desktop-key-1234567890";

describe("IM-05 production App material video WebUI acceptance", () => {
  it("opens the real protected WebUI through the normal product entry", async () => {
    await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");
    await browser
      .$("//li[contains(@class,'ant-menu-item') and .//*[normalize-space()='设置与诊断']]")
      .click();
    const scriptSettings = await browser.$(".model-service-purpose--script");
    await scriptSettings.$("input[aria-label='文案模型服务 API Key']").setValue(TEST_KEY);
    await scriptSettings.$("button=保存配置").click();
    await expect(scriptSettings).toHaveText(expect.stringContaining("已配置"));

    await browser
      .$("//li[contains(@class,'ant-menu-item') and .//*[normalize-space()='视频制作']]")
      .click();
    const studio = await browser.$("section[aria-label='视频制作工作区']");
    const mainHandle = await browser.getWindowHandle();
    await studio.$("button[aria-label='选择智能素材成片']").click();
    await studio.$("button=打开完整制作界面").click();
    await expect(studio).toHaveText(expect.stringContaining("完整制作界面已打开。"));
    assert.doesNotMatch(await browser.$("body").getText(), /127\.0\.0\.1|studio-[A-Za-z0-9_-]{20}/);

    await browser.waitUntil(async () => (await browser.getWindowHandles()).length === 2, {
      timeout: 45_000,
      timeoutMsg: "protected material-video WebUI window was not created",
    });
    const webUiHandle = (await browser.getWindowHandles()).find((handle) => handle !== mainHandle);
    assert.ok(webUiHandle, "protected WebUI handle must be distinct from the main App");
    await browser.switchToWindow(webUiHandle);
    await browser.waitUntil(
      async () => (await browser.$("body").getText()).includes("视频主题"),
      { timeout: 45_000, timeoutMsg: "real material-video form did not become ready" },
    );

    const body = await browser.$("body");
    await expect(body).toHaveText(expect.stringContaining("视频文案"));
    await expect(body).toHaveText(expect.stringContaining("生成视频"));
    const subject = await browser.$("input[aria-label*='视频主题']");
    await subject.setValue("用三十秒解释为什么雨后空气更清新");
    assert.equal(await subject.getValue(), "用三十秒解释为什么雨后空气更清新");
    assert.doesNotMatch(await body.getText(), new RegExp(TEST_KEY));

    await browser.closeWindow();
    await browser.switchToWindow(mainHandle);
    await expect(await browser.$("h2")).toHaveText("视频制作");
  });
});
