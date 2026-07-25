import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

async function openMotionStudio() {
  await browser
    .$("//li[contains(@class,'ant-menu-item') and .//*[normalize-space()='工作台']]")
    .click();
  await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");
  await browser
    .$("//li[contains(@class,'ant-menu-item') and .//*[normalize-space()='视频制作']]")
    .click();
  const studio = await browser.$("section[aria-label='视频制作工作区']");
  await expect(studio).toBeDisplayed();
  await studio.$("button[aria-label='选择品牌动效成片']").click();
  return studio;
}

describe("BM-15 production App motion parts catalog acceptance", () => {
  it("browses 134 parts by Chinese category and overrides a beat selection", async () => {
    const studio = await openMotionStudio();

    await studio.$("div[role='tab']=动效零件").click();
    const browserRegion = await studio.$("section[aria-label='动效零件目录']");
    await expect(browserRegion).toBeDisplayed();
    await expect(browserRegion).toHaveText(
      expect.stringContaining("动效零件目录"),
    );

    const overrides = await studio.$("section[aria-label='分镜零件选用']");
    await expect(overrides).toHaveText(
      expect.stringContaining("动效零件与 12 套整体风格不同"),
    );
    await expect(overrides).toHaveText(expect.stringContaining("第 1 段：已选 0 项"));

    // Category filter narrows the grid to the Chinese category.
    await browserRegion
      .$("//label[contains(@class,'ant-radio-button-wrapper') and contains(., '数据与地图')]")
      .click();
    const cardCount = await browserRegion.$$("li.motion-parts-card").length;
    assert.ok(cardCount > 0 && cardCount < 134, `filtered card count: ${cardCount}`);

    // The audited attributes are visible on a real card.
    const card = await browserRegion.$(
      "//li[contains(@class,'motion-parts-card') and .//*[normalize-space()='数据图表动画']]",
    );
    await expect(card).toBeDisplayed();
    await expect(card).toHaveText(expect.stringContaining("性能：轻量"));
    await expect(card).toHaveText(expect.stringContaining("设备：任意设备"));
    await expect(card).toHaveText(expect.stringContaining("适用：数据指标与地理信息"));
    await expect(card).toHaveText(expect.stringContaining("来源：文字已本地化"));

    // Raw ids and upstream indicator words never reach the page.
    const pageText = await studio.getText();
    assert.doesNotMatch(pageText, /apple-money-count|data-chart(?![a-z-])/);

    // Override: add the part to beat 1 and confirm the selection sticks
    // across page switches.
    await card.$("button=加入第 1 段").click();
    await expect(overrides).toHaveText(expect.stringContaining("第 1 段：已选 1 项"));
    await studio.$("div[role='tab']=脚本与分镜").click();
    await studio.$("div[role='tab']=动效零件").click();
    await expect(
      await studio.$("section[aria-label='分镜零件选用']"),
    ).toHaveText(expect.stringContaining("第 1 段：已选 1 项"));

    // Deterministic per-beat auto recommendation stays within bounds.
    await (
      await studio.$("section[aria-label='分镜零件选用']")
    )
      .$$("button=自动推荐")[1]!
      .click();
    await expect(
      await studio.$("section[aria-label='分镜零件选用']"),
    ).toHaveText(expect.stringMatching(/第 2 段：已选 [1-3] 项/));
  });
});
