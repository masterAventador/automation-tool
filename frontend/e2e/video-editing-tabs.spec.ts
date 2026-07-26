import { expect, test, type Page } from "@playwright/test";

/**
 * 视频剪辑 的「时间轴编辑」与「预览」两个页签。
 *
 * These two tabs had no test of any kind, and no one had ever seen them,
 * because reaching them requires an editing project and the UI Harness could
 * not create one: `harness/main.tsx` passed no `videoEditingGateway`, so the
 * shell fell back to its own `shellVideoEditingGateway`, whose `createProject`
 * throws `draft_storage_unavailable`. The production entry (`src/main.tsx`)
 * has always passed `createLocalVideoEditingGateway(window.sessionStorage)` —
 * a gateway that needs nothing but the browser — so the harness was strictly
 * less capable than the product for no reason.
 *
 * Viewport is 1280x800, the size the production Tauri window opens at
 * (`src-tauri/tauri.conf.json`), pinned below rather than inherited.
 */

/**
 * Pinned, because the shared config does not actually deliver what it says.
 *
 * `playwright.config.ts` declares `viewport: {1280, 800}` at the top level, but
 * its one project spreads `devices["Desktop Chrome"]`, and a project-level
 * `use` wins — that preset carries 1280x720, so every spec in this directory
 * runs 80px shorter than the file headers claim (open as T96). Width is 1280
 * either way, so the measurements below are unaffected, but the number this
 * file reports has to be the number it ran at. Pinned here rather than in the
 * shared config, which would quietly relax the fold line for every other spec.
 */
test.use({ viewport: { width: 1280, height: 800 } });

const HARNESS = "/harness.html?health=available";

const PROJECT_TITLE = "页签宽度验收项目";

/**
 * Source references have to be canonical UUIDv4.
 *
 * `editingProjectSchema.sourceArtifactIds` is an array of `resourceIdSchema`,
 * so a friendly-looking "artifact-001" is rejected as `invalid_project` and no
 * project is created. Real artifact ids come from 视频制作 output, which is
 * where these two stand in for.
 */
const SOURCE_ARTIFACTS = [
  "6f1a2b3c-4d5e-4f60-8a1b-2c3d4e5f6071",
  "7a2b3c4d-5e6f-4071-9b2c-3d4e5f607182",
] as const;

interface TabGeometry {
  /** The column the page content is given, in CSS pixels. */
  readonly column: number;
  /** The outermost element the tab renders — a `Card`, on both these tabs. */
  readonly card: number;
  /** The card's content box, i.e. what the card offers whatever is inside it. */
  readonly cardInner: number;
  /** Named containers inside the card, by class, in nesting order. */
  readonly inner: Record<string, number>;
}

/**
 * Measures the active tab at both levels that matter.
 *
 * Both, because widening only the outer one is worse than widening neither: on
 * 剪辑项目 that produced a full-width card with its right half empty, which
 * passed an outer-width assertion while looking wrong on screen.
 */
function tabGeometry(page: Page, innerSelectors: readonly string[]): Promise<TabGeometry> {
  return page.evaluate((selectors) => {
    const widthOf = (element: Element | null): number =>
      element === null ? -1 : element.getBoundingClientRect().width;
    const main = document.querySelector(".desktop-content main");
    const panel = document.querySelector(
      '.video-editing [role="tabpanel"]:not([aria-hidden="true"])',
    );
    if (main === null || panel === null) {
      throw new Error("视频剪辑 的当前页签没有渲染出来");
    }
    const card = panel.querySelector(".video-editing-panel");
    const body = panel.querySelector(".ant-card-body");
    if (card === null || body === null) {
      throw new Error("当前页签里没有找到卡片");
    }
    const padding = getComputedStyle(body);
    const inner: Record<string, number> = {};
    for (const selector of selectors) {
      inner[selector] = widthOf(panel.querySelector(selector));
    }
    return {
      column: widthOf(main),
      card: widthOf(card),
      cardInner:
        body.clientWidth -
        parseFloat(padding.paddingLeft) -
        parseFloat(padding.paddingRight),
      inner,
    };
  }, innerSelectors);
}

/** Asserts a tab uses every pixel it is given, outside and inside the card. */
function expectTabFillsItsColumn(tab: string, geometry: TabGeometry): void {
  expect(
    Math.round(geometry.card),
    `${tab}：卡片只用了内容列 ${Math.round(geometry.column)}px 中的 ${Math.round(geometry.card)}px`,
  ).toBeGreaterThanOrEqual(Math.floor(geometry.column) - 1);

  for (const [selector, width] of Object.entries(geometry.inner)) {
    expect(width, `${tab}：卡片里找不到 ${selector}`).toBeGreaterThan(0);
    expect(
      Math.round(width),
      `${tab}：${selector} 只用了卡片 ${Math.round(geometry.cardInner)}px 中的 ${Math.round(width)}px`,
    ).toBeGreaterThanOrEqual(Math.floor(geometry.cardInner) - 1);
  }
}

/**
 * Walks the real user path: sidebar → create a project → open its timeline.
 *
 * Deliberately goes through the form rather than seeding storage, because the
 * thing under test is whether an operator can get here at all.
 */
async function openTimelineTab(page: Page): Promise<void> {
  await page.goto(HARNESS);
  await page.getByRole("menuitem", { name: "视频剪辑" }).click();
  await page.getByLabel("剪辑项目标题").fill(PROJECT_TITLE);
  await page.getByLabel("输入素材引用").fill(SOURCE_ARTIFACTS.join("\n"));
  await page.getByRole("button", { name: "创建剪辑项目" }).click();
  await expect(page.getByText(`已创建剪辑项目：${PROJECT_TITLE}`)).toBeVisible();
  await page.getByRole("tab", { name: "时间轴编辑" }).click();
}

/** The currently visible tab panel's text. */
function activePanelText(page: Page): Promise<string> {
  return page
    .locator('.video-editing [role="tabpanel"]:not([aria-hidden="true"])')
    .innerText();
}

test.describe("the two tabs behind a project can be reached at all", () => {
  test("时间轴编辑 shows the timeline editor once a project exists", async ({ page }) => {
    await openTimelineTab(page);

    const text = await activePanelText(page);

    expect(text, "「时间轴编辑」页签没有显示可编辑的时间轴").toContain("保存时间轴");
  });

  test("预览 shows the structure preview once a timeline is saved", async ({ page }) => {
    await openTimelineTab(page);
    await page.getByRole("button", { name: "保存时间轴" }).click();
    await page.getByRole("tab", { name: "预览" }).click();

    const text = await activePanelText(page);

    expect(text, "「预览」页签没有显示时间轴结构预览").toContain("总时长");
  });
});

/**
 * These two tabs were expected to be collapsed like 剪辑项目 and 提交与任务 were,
 * and they are not. Measured at 1280x800 they use the full 992px column and the
 * full 942px card, for two structural reasons worth pinning down:
 *
 *  - their outermost element is a `Card`, a block-level div, so it fills the
 *    column without being told to — unlike 剪辑项目 and 提交与任务, whose outermost
 *    element is a shrink-wrapping `Space`;
 *  - the `Space` one level in is a direct child of `.ant-card-body`, so it is
 *    already carried by `.video-editing-panel .ant-card-body > .ant-space`,
 *    the rule T88 deliberately scoped to the panel instead of naming each card.
 *
 * Both reasons are one refactor away from silently stopping being true, which
 * is what these tests are for.
 */
test.describe("视频剪辑 的两个内页页签填满它们拿到的宽度", () => {
  test("时间轴编辑 tab", async ({ page }) => {
    await openTimelineTab(page);

    const geometry = await tabGeometry(page, [
      ".video-editing-timeline",
      ".video-editing-track",
      ".video-editing-clip",
    ]);

    expectTabFillsItsColumn("时间轴编辑", geometry);
  });

  test("预览 tab", async ({ page }) => {
    await openTimelineTab(page);
    await page.getByRole("button", { name: "保存时间轴" }).click();
    await page.getByRole("tab", { name: "预览" }).click();

    const geometry = await tabGeometry(page, [
      ".video-editing-preview",
      ".video-editing-preview-clips",
    ]);

    expectTabFillsItsColumn("预览", geometry);
  });
});
