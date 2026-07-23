import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { browser, expect } from "@wdio/globals";

interface MotionStylePresetContract {
  readonly presets: ReadonlyArray<{
    readonly id: string;
    readonly displayName: string;
  }>;
}

const contract = JSON.parse(
  readFileSync(
    resolve(process.cwd(), "../contracts/video/motion-style-presets.v1.json"),
    "utf-8",
  ),
) as MotionStylePresetContract;

describe("BM-06 production App motion style catalog acceptance", () => {
  it("lists all twelve Chinese styles with keyboard-accessible selection", async () => {
    await browser
      .$("//li[contains(@class,'ant-menu-item') and .//*[normalize-space()='工作台']]")
      .click();
    await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");
    await browser
      .$("//li[contains(@class,'ant-menu-item') and .//*[normalize-space()='视频制作']]")
      .click();

    const studio = await browser.$("section[aria-label='视频制作工作区']");
    await expect(studio).toBeDisplayed();

    await studio.$("div[role='tab']=制作设置").click();
    await expect(studio).toHaveText(expect.stringContaining("尚未选择制作方式"));
    assert.equal(
      await studio.$("div[role='radiogroup'][aria-label='选择整体画面风格']").isExisting(),
      false,
      "style catalog must stay gated before the brand motion method is selected",
    );

    await studio.$("div[role='tab']=新建视频").click();
    await studio.$("button[aria-label='选择品牌动效成片']").click();
    await studio.$("div[role='tab']=制作设置").click();

    const group = await studio.$("div[role='radiogroup'][aria-label='选择整体画面风格']");
    await expect(group).toBeDisplayed();
    await expect(studio).toHaveText(expect.stringContaining("尚未选择风格"));

    const radios = await group.$$("div[role='radio']");
    assert.equal(radios.length, 12, "catalog must expose exactly twelve styles");
    assert.equal(contract.presets.length, 12, "locked contract must expose twelve presets");
    for (let index = 0; index < contract.presets.length; index += 1) {
      const preset = contract.presets[index]!;
      const radio = radios[index]!;
      assert.equal(
        await radio.getAttribute("aria-label"),
        preset.displayName,
        `style ${index} must use the contract Chinese name`,
      );
      assert.equal(await radio.getAttribute("aria-checked"), "false");
      await expect(radio).toHaveText(expect.stringContaining("适用场景"));
      await expect(radio).toHaveText(expect.stringContaining("风格标签"));
      await expect(radio).toHaveText(expect.stringContaining("示意预览"));
    }

    const first = contract.presets[0]!;
    const second = contract.presets[1]!;
    await radios[0]!.click();
    assert.equal(await radios[0]!.getAttribute("aria-checked"), "true");
    await expect(studio).toHaveText(
      expect.stringContaining(`已选择风格：${first.displayName}`),
    );

    await browser.keys(["ArrowRight"]);
    await browser.keys(["Enter"]);
    assert.equal(await radios[1]!.getAttribute("aria-checked"), "true");
    assert.equal(await radios[0]!.getAttribute("aria-checked"), "false");
    await expect(studio).toHaveText(
      expect.stringContaining(`已选择风格：${second.displayName}`),
    );

    let checkedCount = 0;
    for (let index = 0; index < radios.length; index += 1) {
      if ((await radios[index]!.getAttribute("aria-checked")) === "true") {
        checkedCount += 1;
      }
    }
    assert.equal(checkedCount, 1, "exactly one style may stay selected");

    const body = await browser.$("body").getText();
    assert.doesNotMatch(body, /moneyprinter|hyperframes|b-roll/i);
    assert.doesNotMatch(
      body,
      /biennale|blockframe|blue-professional|bold-poster|broadside|capsule|cartesian|cobalt-grid|coral|creative-mode|daisy-days|editorial-forest|code-editorial/i,
    );

    // 回到工作台首页，避免把页面状态遗留给后续验收用例。
    await browser
      .$("//li[contains(@class,'ant-menu-item') and .//*[normalize-space()='工作台']]")
      .click();
    await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");
  });
});
