import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { browser, expect } from "@wdio/globals";
import {
  openSettings,
  openWorkbenchSection,
  waitForStartup,
} from "./navigation";

interface RealAliyunCredentials {
  readonly accessKeyId: string;
  readonly accessKeySecret: string;
  readonly region: string;
}

function loadRealCredentials(): RealAliyunCredentials {
  const secretsFile = process.env["VE04_SECRETS_FILE"];
  if (secretsFile === undefined || secretsFile.length === 0) {
    throw new Error(
      "VE-04 acceptance requires VE04_SECRETS_FILE pointing at the local credentials JSON",
    );
  }
  const parsed: unknown = JSON.parse(readFileSync(secretsFile, "utf8"));
  const record = parsed as Record<string, unknown>;
  const accessKeyId = record["accessKeyId"];
  const accessKeySecret = record["accessKeySecret"];
  const region = record["region"];
  if (
    typeof accessKeyId !== "string" ||
    typeof accessKeySecret !== "string" ||
    region !== "cn-beijing"
  ) {
    throw new Error("VE-04 credentials file is missing accessKeyId/accessKeySecret/region");
  }
  return { accessKeyId, accessKeySecret, region };
}

/**
 * 这个 spec 接受两种开机结局：直接进工作台，或停在启动修复关口。
 * 停在修复关口时要先点进去，否则侧边栏根本不存在——这一段原本在本文件自己的
 * `openSettings` 里，抽到共享模块时不能连带丢掉。
 */
async function waitForApp(): Promise<void> {
  if ((await waitForStartup({ allowRepair: true })) === "repair") {
    await browser.$("button=打开本地修复工具").click();
  }
}

describe("VE-04 hidden App real Aliyun editing-service acceptance", () => {
  it("saves real credentials, reloads them and passes a real gateway connection test", async () => {
    const credentials = loadRealCredentials();
    await waitForApp();
    await openSettings();

    const card = await browser.$(".video-editing-service-settings-card");
    await expect(card).toHaveText(expect.stringContaining("未配置"));

    // 选择与真实 bucket 同地域的「华北2（北京）」。
    await browser.execute(() => {
      const input = document.querySelector<HTMLInputElement>(
        "input[aria-label='视频剪辑服务地域']",
      );
      const select = input?.closest<HTMLElement>(".ant-select");
      if (select === undefined || select === null) {
        throw new Error("video editing region Select root is missing");
      }
      select.dispatchEvent(
        new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window }),
      );
    });
    const beijingOption = await browser.$(".ant-select-item-option[title='华北2（北京）']");
    await beijingOption.waitForExist({ timeout: 5_000 });
    await beijingOption.click();
    await browser.waitUntil(
      async () => (await card.getText()).includes("华北2（北京）"),
      { timeout: 5_000, timeoutMsg: "region selection did not switch to 华北2（北京）" },
    );

    await card.$("input[aria-label='阿里云 AccessKey ID']").setValue(credentials.accessKeyId);
    await card
      .$("input[aria-label='阿里云 AccessKey Secret']")
      .setValue(credentials.accessKeySecret);
    await card.$("button=保存配置").click();
    await expect(card).toHaveText(expect.stringContaining("已配置"));

    // 保存后输入框清空，密钥不回显、不进入页面文本。
    assert.equal(await card.$("input[aria-label='阿里云 AccessKey ID']").getValue(), "");
    assert.equal(await card.$("input[aria-label='阿里云 AccessKey Secret']").getValue(), "");
    let bodyText = await browser.$("body").getText();
    assert.equal(bodyText.includes(credentials.accessKeyId), false);
    assert.equal(bodyText.includes(credentials.accessKeySecret), false);

    // 重启渲染层后凭据仍在本机受保护存储中。
    await browser.refresh();
    await waitForApp();
    await openSettings();
    const reloadedCard = await browser.$(".video-editing-service-settings-card");
    await expect(reloadedCard).toHaveText(expect.stringContaining("已配置"));
    await expect(reloadedCard).toHaveText(expect.stringContaining("华北2（北京）"));

    // 真实连接测试：正式 Rust 网络桥对真实阿里云网关发起签名请求。
    await reloadedCard.$("button=测试连接").click();
    const successAlert = await reloadedCard.$(".ant-alert-success");
    await successAlert.waitForDisplayed({
      timeout: 45_000,
      timeoutMsg: "real gateway connection test did not report success",
    });
    await expect(successAlert).toHaveText(
      expect.stringContaining("连接成功；访问密钥与所选地域可用。"),
    );
    bodyText = await browser.$("body").getText();
    assert.equal(bodyText.includes(credentials.accessKeyId), false);
    assert.equal(bodyText.includes(credentials.accessKeySecret), false);

    // 清除配置，验收结束不在隔离 App 数据中留下真实凭据。
    await reloadedCard.$("button=清除配置").click();
    await expect(reloadedCard).toHaveText(expect.stringContaining("未配置"));

    // 回到助理页，避免把页面状态遗留给后续验收用例——常驻 App 在多个 spec
    // 之间是共享的。`waitForStartup` 只等不导航，用它做这件事等于没做。
    await openWorkbenchSection("AI 助理");
  });
});
