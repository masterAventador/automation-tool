import assert from "node:assert/strict";

import { expect } from "@wdio/globals";
import {
  openVideoStudio,
  waitForStartup,
} from "./navigation";

async function openMotionStudio() {
  await waitForStartup();
  // `waitForStartup` 只等不导航；改版后进工作区要走 创作 → 分段 → 打开完整制作面板。
  const studio = await openVideoStudio();
  await studio.$("button[aria-label='选择品牌动效成片']").click();
  return studio;
}

describe("BM-15 production App motion parts catalog acceptance", () => {
  it("browses 134 parts by Chinese category and refuses to pretend a tick counts", async () => {
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

    // 固定模板手工制作 is the only mode the App can submit and its renderer
    // never reads a part id, so the page has to say so before anything can be
    // ticked, and no per-beat count may be presented as if it mattered.
    await expect(studio).toHaveText(
      expect.stringContaining("本次制作方式不会用到零件选择"),
    );
    await expect(overrides).toHaveText(
      expect.stringContaining("第 1 段：本次制作不使用零件"),
    );
    assert.doesNotMatch(await overrides.getText(), /已选/);

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

    // Every selection action is withheld rather than silently ignored.
    assert.equal(
      await browserRegion.$$("button=加入第 1 段").length,
      0,
      "固定模板手工制作下不得提供“加入第 N 段”按钮",
    );
    const withheld = await card.$("button=本次制作不使用");
    await expect(withheld).toBeDisabled();
    const recommendButtons = await overrides.$$("button=自动推荐");
    const recommendCount = await recommendButtons.length;
    assert.ok(recommendCount > 0, "分镜零件选用必须为每段渲染“自动推荐”按钮");
    for (let index = 0; index < recommendCount; index += 1) {
      await expect(recommendButtons[index]!).toBeDisabled();
    }

    // The catalog itself stays fully browsable: the parts exist, they are just
    // not wired into this creation path yet.
    await studio.$("div[role='tab']=脚本与分镜").click();
    await studio.$("div[role='tab']=动效零件").click();
    await expect(
      await studio.$("section[aria-label='动效零件目录']"),
    ).toBeDisplayed();
  });
});
