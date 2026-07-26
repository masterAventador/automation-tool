import { expect, test, type Page } from "@playwright/test";
import { MINIMUM_WINDOW } from "./production-window";

/**
 * 视频制作: whether the first step of making a video is reachable without
 * scrolling.
 *
 * The viewport is the shared one from `playwright.config.ts`, which reads the
 * production Tauri window out of `src-tauri/tauri.conf.json` — 1280x800, the
 * size a customer gets on first launch.
 *
 * It used to be pinned in this file, because the shared config declared 800 and
 * then threw it away: its one project spread `devices["Desktop Chrome"]`, whose
 * own 1280x720 wins over the top-level `use`, so every spec in this folder ran
 * 80px shorter than its header claimed. T96 fixed the config and added
 * `e2e/viewport-baseline.spec.ts`, which asserts the delivered viewport against
 * `tauri.conf.json` — so the local pin became a second copy of a number that is
 * now guarded in one place, and was removed. The fold assertions below are
 * unaffected by that removal: they ran at 800 pinned, and run at 800 inherited.
 *
 * Measured on 2026-07-26 before this file existed: the two 选择 buttons on the
 * 新建视频 tab sat at y=1043 and y=1066 against a 800px viewport, so the first
 * thing anyone could actually press was 243px below the fold and the opening
 * screen of the product's headline feature carried no action at all — a
 * 466px-tall `<dl>` of explanatory rows stood between the card's summary and
 * its button.
 *
 * The explanation itself is not the problem and is not what these tests police;
 * they only pin where the *action* sits relative to it. Nothing here asserts on
 * wording, colour, or how much text a card carries.
 */

const HARNESS = "/harness.html?health=available";

/** Both cards in 选择制作方式, by the accessible name each button carries. */
const METHOD_BUTTONS = ["选择智能素材成片", "选择品牌动效成片"] as const;

async function openVideoStudio(page: Page): Promise<void> {
  await page.goto(HARNESS);
  await page.getByRole("menuitem", { name: "视频制作" }).click();
  await expect(page.getByRole("tab", { name: "新建视频" })).toBeVisible();
}

/**
 * Actions the operator can press on this screen without scrolling.
 *
 * Tab labels are excluded on purpose. They navigate within the page the
 * operator is already looking at, so a screen carrying nothing but the tab bar
 * is exactly the screen this file was written about: it looks interactive and
 * offers no way to start the job. Disabled buttons are excluded for the same
 * reason — 打开完整制作界面 renders greyed out until a method is chosen, and a
 * button that cannot be pressed is not a way in.
 */
function actionsInFirstScreen(page: Page): Promise<{ label: string; y: number }[]> {
  return page.locator(".desktop-content main").evaluate((main) => {
    const fold = window.innerHeight;
    return Array.from(main.querySelectorAll("button"))
      .filter((button) => button.closest(".ant-tabs-nav") === null)
      .filter((button) => !button.disabled)
      .map((button) => {
        const box = button.getBoundingClientRect();
        return {
          label: (button.getAttribute("aria-label") ?? button.textContent ?? "").trim(),
          y: Math.round(box.y),
          bottom: Math.round(box.bottom),
          visible: box.width > 0 && box.height > 0,
        };
      })
      .filter((action) => action.visible && action.bottom <= fold)
      .map(({ label, y }) => ({ label, y }));
  });
}

test.describe("视频制作 opens on a screen the operator can act on", () => {
  test("首屏至少有一个可按下的动作", async ({ page }) => {
    await openVideoStudio(page);

    const actions = await actionsInFirstScreen(page);

    expect(
      actions,
      `「视频制作」首屏没有任何可按下的动作，只有页签：${JSON.stringify(actions)}`,
    ).not.toEqual([]);
  });

  for (const name of METHOD_BUTTONS) {
    test(`「${name}」在首屏之内`, async ({ page }) => {
      await openVideoStudio(page);

      const button = page.getByRole("button", { name });
      const box = (await button.boundingBox())!;
      const fold = page.viewportSize()!.height;

      expect(
        Math.round(box.y + box.height),
        `「${name}」底边在 y=${Math.round(box.y + box.height)}，` +
          `折叠线在 ${fold}，要往下滚 ${Math.round(box.y + box.height - fold)}px 才够得着`,
      ).toBeLessThanOrEqual(fold);
    });
  }

  /**
   * The action comes before the explanation inside its own card.
   *
   * Kept separate from the fold assertions above because it is the thing that
   * keeps them true: the `<dl>` grows every time a row is added to
   * `VIDEO_CREATION_METHODS`, and any button sitting after it inherits that
   * growth. Pinning the order means a future row cannot quietly push the button
   * back under the fold.
   */
  for (const name of METHOD_BUTTONS) {
    test(`「${name}」排在该卡片的详细说明之前`, async ({ page }) => {
      await openVideoStudio(page);

      const offsets = await page
        .getByRole("button", { name })
        .evaluate((button) => {
          const card = button.closest(".video-method-card")!;
          const details = card.querySelector(".video-method-details")!;
          return {
            button: Math.round(button.getBoundingClientRect().y),
            details: Math.round(details.getBoundingClientRect().y),
          };
        });

      expect(
        offsets.button,
        `「${name}」在 y=${offsets.button}，卡片里的详细说明在 y=${offsets.details}——` +
          `说明排在了动作前面`,
      ).toBeLessThan(offsets.details);
    });
  }
});

/**
 * The same question at the smallest window the product allows.
 *
 * `tauri.conf.json` sets `minWidth` 960 / `minHeight` 640 — read below rather
 * than copied — so this is a size a user can genuinely drag the window down to,
 * and it is the worst case for this screen: the cards are narrower, so the
 * summary above the button wraps onto more lines and pushes it further down
 * while the fold rises by 160px.
 *
 * This is also where the real margin lives. Re-measured on 2026-07-27 after the
 * T96 viewport fix: at the 800 default both buttons end at y=589, a slack 211px
 * above the fold; at this minimum they end at y=611 against a 640 fold — 29px.
 * The default-size assertions are the regression guard, this one is the edge.
 */
test.describe("窗口拖到最小时首屏仍有可按下的动作", () => {
  test.use({ viewport: MINIMUM_WINDOW });

  for (const name of METHOD_BUTTONS) {
    test(`「${name}」在首屏之内`, async ({ page }) => {
      await openVideoStudio(page);

      const box = (await page.getByRole("button", { name }).boundingBox())!;
      const fold = page.viewportSize()!.height;

      expect(
        Math.round(box.y + box.height),
        `最小窗口下「${name}」底边在 y=${Math.round(box.y + box.height)}，折叠线在 ${fold}`,
      ).toBeLessThanOrEqual(fold);
    });
  }
});
