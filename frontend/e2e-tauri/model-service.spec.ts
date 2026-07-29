import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";
import {
  openSettings,
  waitForStartup,
  workbenchIsMounted,
} from "./navigation";

const TEST_KEY = "sk-vf05-invalid-desktop-key-1234567890";

describe("VF-05 hidden App model service acceptance", () => {
  it("persists, reloads, reuses and tests a credential without reflecting it", async () => {
    // 这个 spec 接受两种开机结局：直接进工作台，或停在启动修复关口。
    // 停在修复关口时要先点进去，否则侧边栏根本不存在——这一段原本在本文件
    // 自己的 openSettings 里，抽到共享模块时不能连带丢掉。
    if (await waitForStartup({ allowRepair: true }) === "repair") {
      await browser.$("button=打开本地修复工具").click();
    }
    await openSettings();

    const script = await browser.$(".model-service-purpose--script");
    await expect(script).toHaveText(expect.stringContaining("未配置"));
    await script.$("input[aria-label='文案模型服务 API Key']").setValue(TEST_KEY);
    await script.$("button=保存配置").click();
    await expect(script).toHaveText(expect.stringContaining("已配置"));
    assert.equal(
      await script.$("input[aria-label='文案模型服务 API Key']").getValue(),
      "",
    );
    assert.doesNotMatch(await browser.$("body").getText(), new RegExp(TEST_KEY));

    await browser.refresh();
    await browser.waitUntil(
      async () =>
        (await workbenchIsMounted()) ||
        (await browser.$("button=打开本地修复工具").isExisting()),
      { timeoutMsg: "production App did not recover after refresh" },
    );
    await openSettings();
    const reloadedScript = await browser.$(".model-service-purpose--script");
    await expect(reloadedScript).toHaveText(expect.stringContaining("已配置"));
    assert.equal(
      await reloadedScript.$("input[aria-label='文案模型服务 API Key']").getValue(),
      "",
    );

    await reloadedScript.$("button=测试连接").click();
    await expect(await reloadedScript.$(".ant-alert-error")).toBeDisplayed();
    const errorText = await reloadedScript.$(".ant-alert-error").getText();
    assert.match(
      errorText,
      /密钥未通过阿里百炼验证|暂时无法连接阿里百炼|连接测试超时/,
    );
    assert.doesNotMatch(errorText, /sk-|dashscope|password|Bearer/i);

    await browser.$("button=视频创作复用文案服务密钥").click();
    const video = await browser.$(".model-service-purpose--video_creative");
    await expect(video).toHaveText(expect.stringContaining("已配置"));
    assert.doesNotMatch(await browser.$("body").getText(), new RegExp(TEST_KEY));
  });
});
