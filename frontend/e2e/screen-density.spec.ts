import { expect, test, type Page } from "@playwright/test";

/**
 * Screen density: how much a page actually says in the one screen it gets.
 *
 * The viewport here is Playwright's default 1280x800, which is deliberately the
 * same as the production Tauri window (`src-tauri/tauri.conf.json` declares
 * width 1280, height 800) — the size the customer gets on first launch.
 *
 * Two findings from the 2026-07-26 sweep are pinned here.
 *
 * The first is width. `Space` is `display: inline-flex`, so it shrink-wraps
 * unless something says otherwise. Every other feature in this app says
 * otherwise — `.settings-stack`, `.platform-session-stack`, `.task-create-stack`,
 * `.diagnostics-stack`, `.video-studio-new-form` all declare `width: 100%`.
 * 视频剪辑 was the one feature whose class names were written in the JSX and
 * never given a rule, so its panes sat at 568px inside a 992px column and the
 * right half of the page was blank.
 *
 * The second is the gap under the page title. It is 20px on six pages and 24px
 * on a seventh because each page's root declares its own top margin; 视频剪辑
 * and 作品发布 were the two roots that never got one, so their first element
 * started at the exact y the title row ended — and on both of those pages the
 * first element is an `Alert`, which put a coloured border straight under the
 * status tag.
 */

const HARNESS = "/harness.html?health=available";

/** Every destination in the sidebar, in the order they appear there. */
const SIDEBAR_PAGES = [
  "工作台",
  "新建任务",
  "任务记录",
  "视频制作",
  "视频剪辑",
  "作品发布",
  "平台状态",
  "设置与诊断",
] as const;

/**
 * The narrowest gap that still reads as a gap.
 *
 * The pages that have one use 20px or 24px; this only has to be tight enough to
 * catch a page that has none at all.
 */
const MIN_TITLE_GAP = 16;

/** The column the page content is given, in CSS pixels. */
function contentColumnWidth(page: Page): Promise<number> {
  return page
    .locator(".desktop-content main")
    .evaluate((element) => element.getBoundingClientRect().width);
}

function paneWidth(page: Page, selector: string): Promise<number> {
  return page.locator(selector).evaluate((element) => element.getBoundingClientRect().width);
}

/**
 * Card bodies on this page whose content stops short of the card's own edge.
 *
 * Widening the outer pane alone is not enough and looks worse on its own: the
 * card grows to the full column while the `Space` inside it keeps shrink-wrapping,
 * so the operator gets a wide card with its right half blank. Only `Space`
 * children are examined — `Empty` centres itself and is meant to be narrower.
 */
function narrowCardContents(page: Page): Promise<{ card: number; content: number }[]> {
  return page.locator(".video-editing").evaluate((section) => {
    const findings: { card: number; content: number }[] = [];
    for (const body of Array.from(section.querySelectorAll(".ant-card-body"))) {
      if (body.getBoundingClientRect().width === 0) continue;
      const content = body.firstElementChild;
      if (content === null || !content.classList.contains("ant-space")) continue;
      const style = getComputedStyle(body);
      const inner =
        body.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      const used = content.getBoundingClientRect().width;
      if (used < inner - 1) {
        findings.push({ card: Math.round(inner), content: Math.round(used) });
      }
    }
    return findings;
  });
}

async function openSidebarPage(page: Page, name: string): Promise<void> {
  await page.goto(HARNESS);
  await page.getByRole("menuitem", { name }).click();
  await expect(page.getByRole("menuitem", { name })).toBeVisible();
}

test.describe("视频剪辑 fills the column it is given", () => {
  test("剪辑项目 pane", async ({ page }) => {
    await openSidebarPage(page, "视频剪辑");

    const column = await contentColumnWidth(page);
    const pane = await paneWidth(page, ".video-editing-projects");

    expect(
      Math.round(pane),
      `视频剪辑「剪辑项目」页签只用了内容列 ${Math.round(column)}px 中的 ${Math.round(pane)}px`,
    ).toBeGreaterThanOrEqual(Math.floor(column) - 1);
  });

  test("提交与任务 pane", async ({ page }) => {
    await openSidebarPage(page, "视频剪辑");
    await page.getByRole("tab", { name: "提交与任务" }).click();

    const column = await contentColumnWidth(page);
    const pane = await paneWidth(page, ".video-editing-jobs");

    expect(
      Math.round(pane),
      `视频剪辑「提交与任务」页签只用了内容列 ${Math.round(column)}px 中的 ${Math.round(pane)}px`,
    ).toBeGreaterThanOrEqual(Math.floor(column) - 1);
  });

  test("剪辑项目 card contents", async ({ page }) => {
    await openSidebarPage(page, "视频剪辑");

    const narrow = await narrowCardContents(page);

    expect(narrow, `视频剪辑「剪辑项目」卡片里的内容没有填满卡片：${JSON.stringify(narrow)}`)
      .toEqual([]);
  });

  test("提交与任务 card contents", async ({ page }) => {
    await openSidebarPage(page, "视频剪辑");
    await page.getByRole("tab", { name: "提交与任务" }).click();

    const narrow = await narrowCardContents(page);

    expect(narrow, `视频剪辑「提交与任务」卡片里的内容没有填满卡片：${JSON.stringify(narrow)}`)
      .toEqual([]);
  });
});

test.describe("the page body starts below the title row, not against it", () => {
  for (const name of SIDEBAR_PAGES) {
    test(name, async ({ page }) => {
      await openSidebarPage(page, name);

      const gap = await page.locator(".desktop-content main").evaluate((main) => {
        const titleRow = main.children[0];
        const firstBlock = main.children[1];
        if (titleRow === undefined || firstBlock === undefined) return null;
        return Math.round(
          firstBlock.getBoundingClientRect().top - titleRow.getBoundingClientRect().bottom,
        );
      });

      expect(gap, `${name}：页面没有渲染出标题行之后的内容块`).not.toBeNull();
      expect(gap!, `${name}：标题行与首块内容之间只有 ${gap}px`).toBeGreaterThanOrEqual(
        MIN_TITLE_GAP,
      );
    });
  }
});
