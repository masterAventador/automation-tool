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
  it("browses 134 parts and makes a one-sentence shot override explicit", async () => {
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

    // A selection is a pre-authoring override for the next one-sentence run.
    // The page has to name that ownership boundary before it offers a button:
    // unselected shots remain the model's, while the fixed-template preview
    // one tab away does not consume this state.
    await expect(studio).toHaveText(
      expect.stringContaining("这些指定只用于下一次“一句话自动制作”"),
    );
    await expect(overrides).toHaveText(
      expect.stringContaining("第 1 镜头：由模型自动选择"),
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

    // A component without frozen text slots is still a real visual part. It
    // must own the same per-shot override action as the 37 copy-bearing parts
    // and reach the production component host instead of becoming browse-only.
    await browserRegion
      .$("//label[contains(@class,'ant-radio-button-wrapper') and contains(., '文字效果')]")
      .click();
    const visualOnlyCard = await browserRegion.$(
      "//li[contains(@class,'motion-parts-card') and .//*[normalize-space()='反色叠加文字']]",
    );
    await expect(visualOnlyCard).toBeDisplayed();

    // The real catalog card now owns the next one-sentence film's first shot.
    // One shot accepts one part because film assembly consumes one part per
    // shot; choosing a second card replaces this selection rather than
    // presenting a count the renderer cannot honour.
    const select = await visualOnlyCard.$("button=指定给第 1 镜头");
    await expect(select).toBeEnabled();
    await select.click();
    await expect(overrides).toHaveText(
      expect.stringContaining("第 1 镜头：已指定反色叠加文字"),
    );
    await expect(
      visualOnlyCard.$("button=取消第 1 镜头的指定"),
    ).toBeEnabled();

    const recommendButtons = await overrides.$$("button=自动推荐");
    const recommendCount = await recommendButtons.length;
    assert.ok(recommendCount > 0, "分镜零件选用必须为每个镜头渲染“自动推荐”按钮");
    for (let index = 0; index < recommendCount; index += 1) {
      await expect(recommendButtons[index]!).toBeEnabled();
    }

    // The neighbouring preview states the other half of the boundary rather
    // than suggesting that this selection changes the fixed template.
    await studio.$("div[role='tab']=预览").click();
    await expect(studio).toHaveText(
      expect.stringContaining("这里预览和提交的是固定模板手工制作"),
    );
    await studio.$("div[role='tab']=动效零件").click();
    await expect(overrides).toHaveText(
      expect.stringContaining("第 1 镜头：已指定反色叠加文字"),
    );
  });
});
