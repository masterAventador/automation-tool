import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { browser, expect } from "@wdio/globals";
import {
  openTaskCreate,
  waitForStartup,
  openWorkbenchSection,
} from "./navigation";

interface TerminologyContract {
  readonly videoCreationMethods: ReadonlyArray<{ readonly displayName: string }>;
  readonly videoCreationMethodCardLabels: readonly string[];
}

const CONTRACT = JSON.parse(
  readFileSync(resolve("../contracts/quality/user-facing-terminology.v1.json"), "utf-8"),
) as TerminologyContract;

/**
 * Pages captured from the real App. `scripts/run_cq_01_acceptance.py` feeds
 * this file back through `check_user_facing_branding.py`, so the industry term
 * rule keeps exactly one implementation and the real rendered text is judged
 * by the same matcher as the source scan.
 */
const CAPTURE_FILE = process.env["CQ01_PAGE_TEXT_FILE"];
const captured: Array<{ page: string; text: string; accessibleNames: string[] }> = [];

/**
 * Records the visible text of the current page together with the accessible
 * names a screen reader would announce (`aria-label`, `alt`, `title`
 * attributes and SVG `<title>` nodes), which `getText()` alone does not cover.
 */
async function capture(page: string): Promise<string> {
  const text = await browser.$("body").getText();
  const accessibleNames = await browser.execute(() => {
    const names: string[] = [];
    const elements = document.querySelectorAll("[aria-label],[alt],[title],title");
    for (let index = 0; index < elements.length; index += 1) {
      const element = elements[index]!;
      const candidates = [
        element.getAttribute("aria-label"),
        element.getAttribute("alt"),
        element.getAttribute("title"),
        element.tagName.toLowerCase() === "title" ? element.textContent : null,
      ];
      for (let slot = 0; slot < candidates.length; slot += 1) {
        const value = (candidates[slot] ?? "").trim();
        if (value !== "") {
          names.push(value);
        }
      }
    }
    return names;
  });
  captured.push({ page, text, accessibleNames });
  return text;
}

async function openStudio() {
  await openWorkbenchSection("视频制作");
  const studio = await browser.$("section[aria-label='视频制作工作区']");
  await expect(studio).toBeDisplayed();
  // VideoStudio intentionally remembers the last active tab across navigation.
  // Each scenario below starts from the creation-method cards, so enter that
  // page explicitly instead of inheriting state from the preceding scenario.
  await studio.$("div[role='tab']=新建视频").click();
  return studio;
}

after(() => {
  if (CAPTURE_FILE !== undefined) {
    writeFileSync(CAPTURE_FILE, JSON.stringify(captured), "utf-8");
  }
});

describe("CQ-01 production App plain-language comprehension acceptance", () => {
  before(async () => {
    // Start this file from a fresh production frontend; later scenarios in the
    // file intentionally exercise state carried through normal navigation.
    await browser.refresh();
  });

  it("lets a non-technical user pick the right creation method", async () => {
    await openWorkbenchSection("工作台");
    await waitForStartup();
    await capture("工作台");

    const studio = await openStudio();

    // Exactly two creation methods, both named in the approved product words.
    const cards = await studio.$$("article.video-method-card");
    const cardCount = await cards.length;
    assert.equal(cardCount, 2, "视频制作只允许出现两种制作方式");

    const cardTexts: string[] = [];
    for (let index = 0; index < cardCount; index += 1) {
      cardTexts.push(await cards[index]!.getText());
    }
    assert.ok(
      cardTexts[0]!.includes("智能素材成片") && cardTexts[1]!.includes("品牌动效成片"),
      `制作方式卡片名称不正确：${cardTexts.map((text) => text.slice(0, 12)).join(" / ")}`,
    );

    // Every comparison question a normal user needs is answered on both cards
    // with rendered Chinese text. The list comes from the terminology contract
    // so it is not copied a third time.
    for (let index = 0; index < CONTRACT.videoCreationMethodCardLabels.length; index += 1) {
      const label = CONTRACT.videoCreationMethodCardLabels[index]!;
      for (let card = 0; card < cardTexts.length; card += 1) {
        assert.ok(
          cardTexts[card]!.includes(label),
          `第 ${card + 1} 张制作方式卡片缺少“${label}”说明`,
        );
      }
    }

    // The two cards must not read the same: suitable/unsuitable/example differ.
    for (let index = 0; index < CONTRACT.videoCreationMethods.length; index += 1) {
      const method = CONTRACT.videoCreationMethods[index]!;
      const card = cardTexts[index]!;
      assert.ok(
        card.includes(method.displayName),
        `第 ${index + 1} 张卡片应展示“${method.displayName}”`,
      );
      const other = cardTexts[1 - index]!;
      const suitable = card.split("最适合")[1]?.split("不适合")[0] ?? "";
      const otherSuitable = other.split("最适合")[1]?.split("不适合")[0] ?? "";
      assert.ok(suitable.trim().length >= 10, `${method.displayName}“最适合”说明过短`);
      assert.notEqual(
        suitable,
        otherSuitable,
        "两种制作方式的“最适合”说明必须不同，否则用户无法选择",
      );
    }

    await capture("视频制作 / 新建视频");

    // Picking a method is reflected in plain Chinese.
    await studio.$("button[aria-label='选择智能素材成片']").click();
    await expect(studio).toHaveText(expect.stringContaining("已选择：智能素材成片"));
  });

  it("keeps 动效零件 tied to 品牌动效成片 instead of showing it for every method", async () => {
    const studio = await openStudio();
    await studio.$("button[aria-label='选择智能素材成片']").click();
    await studio.$("div[role='tab']=动效零件").click();

    // With 智能素材成片 selected, the 134-part catalog must not be offered,
    // because it only belongs to 品牌动效成片.
    const partsText = await studio.getText();
    assert.ok(
      !partsText.includes("动效零件目录"),
      "选择“智能素材成片”时不应展示只属于“品牌动效成片”的 134 个动效零件目录",
    );
    assert.ok(
      partsText.includes("动效零件只属于“品牌动效成片”"),
      "选择“智能素材成片”时必须说明动效零件属于哪种制作方式",
    );
  });

  it("separates 12 套整体风格 from 134 个动效零件", async () => {
    const studio = await openStudio();
    await studio.$("button[aria-label='选择品牌动效成片']").click();

    await studio.$("div[role='tab']=制作设置").click();
    const styles = await studio.$("div[role='radiogroup'][aria-label='选择整体画面风格']");
    await expect(styles).toBeDisplayed();
    await expect(studio).toHaveText(expect.stringContaining("查看全部 12 套风格"));
    await studio.$("button=查看全部 12 套风格").click();
    const styleCards = await studio.$$("div.motion-style-card");
    assert.equal(await styleCards.length, 12, "整体画面风格必须正好展示 12 套");
    await capture("视频制作 / 制作设置");

    await studio.$("div[role='tab']=动效零件").click();
    const overrides = await studio.$("section[aria-label='分镜零件选用']");
    await expect(overrides).toHaveText(
      expect.stringContaining("动效零件与 12 套整体风格不同"),
    );
    const catalog = await studio.$("section[aria-label='动效零件目录']");
    await expect(catalog).toHaveText(expect.stringContaining("动效零件目录"));

    // Every rendered part card carries a Chinese explanation next to its name,
    // so an English part name is never presented on its own.
    const partCards = await catalog.$$("li.motion-parts-card");
    const total = await partCards.length;
    assert.ok(total > 0, "动效零件目录必须渲染零件卡片");
    for (let index = 0; index < total; index += 1) {
      const text = await partCards[index]!.getText();
      assert.match(
        text,
        /适用：[一-鿿]/,
        `第 ${index + 1} 个动效零件缺少中文“适用”说明`,
      );
    }
    await capture("视频制作 / 动效零件");
  });

  it("presents 视频剪辑 as its own module rather than part of 视频制作", async () => {
    await openWorkbenchSection("视频剪辑");
    await expect(await browser.$("h2")).toHaveText("视频剪辑");
    const editing = await browser.$("section[aria-label='视频剪辑工作区']");
    await expect(editing).toBeDisplayed();
    assert.equal(
      await browser.$$("section[aria-label='视频制作工作区']").length,
      0,
      "视频剪辑页面不应同时挂载视频制作工作区",
    );
    await expect(await browser.$("main")).toHaveText(
      expect.stringContaining("独立于视频制作"),
    );

    const editingText = await capture("视频剪辑");
    // The empty-state placeholder used to be antd's English default, which a
    // screen reader announced from the illustration's SVG <title>.
    assert.doesNotMatch(editingText, /No data/i, "空状态不应出现未翻译的英文占位文案");
    const names = captured[captured.length - 1]!.accessibleNames.join("\n");
    assert.doesNotMatch(names, /No data/i, "空状态的无障碍名称不应是英文占位文案");
  });

  it("keeps the settings and platform pages free of raw technical words", async () => {
    await openWorkbenchSection("设置与诊断");
    const diagnostics = await capture("设置与诊断");
    assert.doesNotMatch(
      diagnostics,
      /(?:^|[^0-9A-Za-z])(?:stopped|running|restarting)(?![0-9A-Za-z])/,
      "设置与诊断不应把执行器原始状态码显示给用户",
    );

    await openWorkbenchSection("平台状态");
    await capture("平台状态");

    await openTaskCreate();
    await capture("新建任务");
  });
});
