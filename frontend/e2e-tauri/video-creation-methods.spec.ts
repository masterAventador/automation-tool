import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

describe("VF-07 production App creation method acceptance", () => {
  it("compares and selects exactly two understandable creation methods", async () => {
    await browser
      .$("//li[contains(@class,'ant-menu-item') and .//*[normalize-space()='工作台']]")
      .click();
    await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");
    await browser
      .$("//li[contains(@class,'ant-menu-item') and .//*[normalize-space()='视频制作']]")
      .click();

    const studio = await browser.$("section[aria-label='视频制作工作区']");
    const materialMethod = await studio.$("button[aria-label='选择智能素材成片']");
    const motionMethod = await studio.$("button[aria-label='选择品牌动效成片']");
    await expect(materialMethod).toBeDisplayed();
    await expect(motionMethod).toBeDisplayed();
    await expect(materialMethod).toHaveAttribute("aria-pressed", "false");
    await expect(motionMethod).toHaveAttribute("aria-pressed", "false");

    const comparisonLabels = [
      "最适合",
      "不适合",
      "举个例子",
      "外部服务",
      "本机处理",
      "预计耗时",
      "设备占用",
      "临时磁盘",
      "网络消耗",
      "数据与隐私",
    ] as const;
    for (const label of comparisonLabels) {
      const matches = await studio.$$(`dt=${label}`);
      assert.equal(matches.length, 2, `${label} must be explained by both methods`);
    }

    await materialMethod.click();
    await expect(materialMethod).toHaveAttribute("aria-pressed", "true");
    await expect(motionMethod).toHaveAttribute("aria-pressed", "false");
    await expect(studio).toHaveText(expect.stringContaining("已选择：智能素材成片"));

    await motionMethod.click();
    await expect(materialMethod).toHaveAttribute("aria-pressed", "false");
    await expect(motionMethod).toHaveAttribute("aria-pressed", "true");
    await expect(studio).toHaveText(expect.stringContaining("已选择：品牌动效成片"));
    await expect(await studio.$("button=创建视频草稿")).not.toBeEnabled();

    const body = await browser.$("body").getText();
    assert.doesNotMatch(body, /moneyprinter|hyperframes|b-roll/i);
    assert.doesNotMatch(body, /真人生成|网址转视频/);
  });
});
