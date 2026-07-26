import { expect, test, type Page } from "@playwright/test";

/**
 * 视频制作: whether the first step of making a video is reachable without
 * scrolling.
 *
 * The viewport is pinned to 1280x800 below, which is the production Tauri
 * window (`src-tauri/tauri.conf.json` declares width 1280, height 800) — the
 * size a customer gets on first launch.
 *
 * It has to be pinned here rather than inherited. `playwright.config.ts` does
 * set `use.viewport` to 1280x800, but its only project spreads
 * `devices["Desktop Chrome"]` on top of that, and that preset carries its own
 * 1280x720 — so the config's declared height loses and every spec in this
 * folder actually runs 80px shorter than the window it claims to be measuring.
 * Measured, not read: `page.viewportSize()` returned 720 on the first run of
 * this file. Left alone rather than corrected in the shared config, because
 * lengthening the viewport would relax every other spec's fold at a moment
 * when several lines are editing this app at once.
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

/** The production window, as declared in `src-tauri/tauri.conf.json`. */
test.use({ viewport: { width: 1280, height: 800 } });

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
 * `tauri.conf.json` sets `minWidth` 960 / `minHeight` 640, so this is a size a
 * user can genuinely drag the window down to, and it is the worst case for this
 * screen: the cards are narrower, so the summary above the button wraps onto
 * more lines and pushes it further down while the fold rises by 160px.
 * Measured after the fix, the lower of the two buttons ends at y=611 against a
 * 640 fold — 29px of clearance, thin enough that it is worth a test rather than
 * an assumption.
 */
test.describe("窗口拖到最小时首屏仍有可按下的动作", () => {
  test.use({ viewport: { width: 960, height: 640 } });

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
