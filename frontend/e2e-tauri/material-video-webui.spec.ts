import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

const TEST_KEY = "sk-im05-invalid-desktop-key-1234567890";

describe("IM-05/IM-06 production App material video WebUI acceptance", () => {
  it("opens the real protected and product-themed WebUI through the normal product entry", async () => {
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
    assert.match(await browser.getUrl(), /^http:\/\/127\.0\.0\.1:/);
    try {
      await browser.waitUntil(
        async () => {
          const state = await browser.execute(() =>
            document.documentElement.getAttribute("data-automation-tool-studio-state"),
          );
          if (state === "failed") throw new Error("embedded theme guard failed closed");
          return (await browser.$("body").getText()).includes("视频主题") && state === "ready";
        },
        { timeout: 150_000, timeoutMsg: "real material-video form did not become ready" },
      );
    } catch (error) {
      const diagnostics = await browser.execute(() => ({
        body: document.body?.innerText?.slice(0, 1000) || "",
        containers: document.querySelectorAll('[data-testid="stAppViewContainer"]').length,
        generateButtons: Array.from(document.querySelectorAll("button")).filter((button) =>
          /生成视频|Generate Video/i.test(button.textContent || ""),
        ).length,
        subjectInputs: document.querySelectorAll(
          'input[aria-label*="视频主题"], input[aria-label*="Video Subject" i]',
        ).length,
        state: document.documentElement.getAttribute("data-automation-tool-studio-state"),
        failure: document.documentElement.getAttribute("data-automation-tool-studio-failure"),
        title: document.title,
        url: window.location.href,
      }));
      const symptom = new Error(
        `real material-video form did not become ready: ${JSON.stringify(diagnostics)}`,
      ) as Error & { cause: unknown };
      symptom.cause = error;
      throw symptom;
    }
    const body = await browser.$("body");
    assert.equal(await browser.getTitle(), "智能素材成片");
    await expect(body).toHaveText(expect.stringContaining("视频文案"));
    await expect(body).toHaveText(expect.stringContaining("生成视频"));
    await expect(body).toHaveText(expect.stringContaining("智能素材成片"));
    assert.doesNotMatch(
      await body.getText(),
      /money\s*[-_ ]?\s*printer\s*[-_ ]?\s*turbo|hyper\s*[-_ ]?\s*frames/i,
    );
    assert.equal(
      await browser.execute(() =>
        Array.from(document.querySelectorAll("a[href]")).filter((anchor) => {
          try {
            return new URL(anchor.getAttribute("href") || "", window.location.href).origin !== window.location.origin;
          } catch {
            return true;
          }
        }).length,
      ),
      0,
    );
    const fontFamily = await browser.execute(() => getComputedStyle(document.body).fontFamily);
    assert.match(fontFamily, /PingFang SC|Microsoft YaHei|Inter/);
    const duplicateTaskManager = await browser.$(".st-key-task_manager_entry");
    if (await duplicateTaskManager.isExisting()) {
      assert.equal(await duplicateTaskManager.isDisplayed(), false);
    }

    const settingsButton = await browser.$("button[aria-label='制作服务设置']");
    await expect(settingsButton).toBeDisplayed();
    await settingsButton.click();
    await browser.waitUntil(async () => (await browser.$("body").getText()).includes("Pexels"), {
      timeout: 15_000,
      timeoutMsg: "material API settings were not preserved",
    });
    await expect(await browser.$("[role='tab']*=素材 API")).toBeDisplayed();
    const modelTabs = await browser.$$("[role='tab']*=大模型设置");
    const modelTabCount = await modelTabs.length;
    for (let index = 0; index < modelTabCount; index += 1) {
      assert.equal(await modelTabs[index].isDisplayed(), false);
    }
    await browser.keys(["Escape"]);

    const subject = await browser.$("input[aria-label*='视频主题']");
    await subject.setValue("用三十秒解释为什么雨后空气更清新");
    assert.equal(await subject.getValue(), "用三十秒解释为什么雨后空气更清新");
    assert.doesNotMatch(await body.getText(), new RegExp(TEST_KEY));

    await browser.closeWindow();
    await browser.switchToWindow(mainHandle);
    await expect(await browser.$("h2")).toHaveText("视频制作");
    await browser.$("div[role='tab']=制作任务").click();
    await expect(await browser.$("body")).toHaveText(expect.stringContaining("还没有真实制作任务"));
    await browser.$("div[role='tab']=成片").click();
    await expect(await browser.$("body")).toHaveText(expect.stringContaining("还没有已导入的成片"));

  });
});
