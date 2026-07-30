import { expect, test, type Page } from "@playwright/test";
import { openVideoStudio as navigateToVideoStudio } from "./navigation";
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
  await navigateToVideoStudio(page);
  await expect(page.getByRole("tab", { name: "新建视频" })).toBeVisible();
}

/**
 * Actions the operator can press on this screen without scrolling.
 *
 * Tab labels are excluded on purpose. They navigate within the page the
 * operator is already looking at, so a screen carrying nothing but the tab bar
 * is exactly the screen this file was written about: it looks interactive and
 * offers no way to start the job. Disabled buttons are excluded for the same
 * reason — the selected method's editor remains inside the current App, and a
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
   *
   * The explanation is now behind a disclosure, and antd only mounts a
   * collapsed panel's content once it is first opened — so this checks the
   * button against the disclosure control while shut, then opens it and checks
   * the rows themselves. Both halves matter: the first is what holds while the
   * page is in the state a customer first meets it, the second is what stops
   * the rows being re-parented above the button later.
   */
  for (const name of METHOD_BUTTONS) {
    test(`「${name}」排在该卡片的详细说明之前`, async ({ page }) => {
      await openVideoStudio(page);

      const card = page.locator(".video-method-card", {
        has: page.getByRole("button", { name }),
      });
      const buttonY = async () =>
        Math.round((await page.getByRole("button", { name }).boundingBox())!.y);
      const disclosureY = Math.round(
        (await card.locator(".video-method-disclosure").boundingBox())!.y,
      );

      expect(
        await buttonY(),
        `「${name}」在 y=${await buttonY()}，详细说明的折叠控件在 y=${disclosureY}——` +
          `说明排在了动作前面`,
      ).toBeLessThan(disclosureY);

      await card.getByRole("button", { name: /详细说明/ }).click();
      const detailsY = Math.round(
        (await card.locator(".video-method-details").boundingBox())!.y,
      );

      expect(
        await buttonY(),
        `展开后「${name}」在 y=${await buttonY()}，说明表在 y=${detailsY}`,
      ).toBeLessThan(detailsY);
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

/**
 * The second pass: the whole tab in one screen, not just its first action.
 *
 * Getting the 选择 buttons above the fold fixed the opening view but left the
 * page 1240px tall against 736px of room — the customer still had 504px of
 * scrolling to see the rest of the step. Three things paid for that height and
 * all three are addressed here.
 */
test.describe("「新建视频」页签一屏装得下", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  /**
   * The page title, the sidebar entry and the tab already say where you are.
   *
   * 「新建视频」was printed twice within 17px of itself — once as the active tab
   * label at y=188 and again as the card's own head at y=254, the latter costing
   * 56px of the one screen this step gets. The tab has to stay: it is how you
   * navigate back to this step from the other six. The card head is the copy
   * that carries no information the tab has not already given.
   */
  test("「新建视频」在页面上只出现一次", async ({ page }) => {
    await openVideoStudio(page);

    const places = await page.locator(".desktop-content main").evaluate((main) =>
      Array.from(main.querySelectorAll("*"))
        .filter((el) => el.children.length === 0)
        .filter((el) => (el.textContent ?? "").trim() === "新建视频")
        .filter((el) => el.getBoundingClientRect().width > 0)
        .map((el) => ({
          where: el.className.toString().slice(0, 40),
          y: Math.round(el.getBoundingClientRect().y),
        })),
    );

    expect(
      places,
      `「新建视频」在同一屏里出现了 ${places.length} 次：${JSON.stringify(places)}`,
    ).toHaveLength(1);
  });

  /**
   * The whole step fits, with no scrolling at all.
   *
   * Measured before: `scrollHeight` 1240 against `clientHeight` 736. The 10-row
   * `<dl>` in each card was 466px of that on its own.
   */
  test("整页不需要滚动", async ({ page }) => {
    await openVideoStudio(page);

    const box = await page
      .locator(".desktop-content")
      .evaluate((el) => ({ scroll: el.scrollHeight, client: el.clientHeight }));

    expect(
      box.scroll,
      `「新建视频」整页 ${box.scroll}px，可视只有 ${box.client}px，还要滚 ${
        box.scroll - box.client
      }px`,
    ).toBeLessThanOrEqual(box.client);
  });

  test("详细说明默认是收起的", async ({ page }) => {
    await openVideoStudio(page);

    await expect(page.locator(".video-method-details").first()).toBeHidden();
  });

  /**
   * The disclosure has to be operable without a mouse and has to say which of
   * the two methods it belongs to.
   *
   * Both requirements come from the same place: there are two of these controls
   * side by side, so a control whose accessible name is just 「详细说明」 leaves
   * a screen reader user with two identical toggles and no way to tell which
   * card each one opens.
   */
  for (const method of ["智能素材成片", "品牌动效成片"] as const) {
    test(`「${method}」的详细说明可以用键盘展开`, async ({ page }) => {
      await openVideoStudio(page);

      /*
       * `exact` matters here. Playwright matches accessible names as substrings
       * by default, and this assertion passed while Chromium was really
       * exposing 「collapsed 智能素材成片的详细说明」 — antd's expand icon
       * carries `aria-label="collapsed"` and name-from-content swallowed it.
       * Exact matching is what makes this test able to see that.
       */
      const toggle = page.getByRole("button", {
        name: `${method}的详细说明`,
        exact: true,
      });
      await expect(toggle).toBeVisible();

      const details = page
        .locator(".video-method-card", { hasText: method })
        .locator(".video-method-details");
      await expect(details).toBeHidden();

      await toggle.focus();
      await expect(toggle).toBeFocused();
      await page.keyboard.press("Enter");

      await expect(details).toBeVisible();
    });
  }
});

/**
 * The empty states, and the 330px they were each given.
 *
 * `.video-studio-panel { min-height: 330px }` and
 * `.video-studio-panel .ant-empty { margin-block: 70px }` arrived together in
 * d331d90 「建立视频制作页面骨架」, alongside `.video-studio-new-form`, at a
 * point when five of the six tabs rendered nothing but a static placeholder and
 * the component had no gateway and no effects at all. They gave six identical
 * skeletons a uniform panel height. Measured before: a 330px card holding a 96px
 * graphic — 234px of it empty.
 *
 * The bound below is what the card costs when the product adds no spacing of its
 * own: 96px of `Empty`, antd's own 32px margin above and below it, and the 24px
 * card body padding top and bottom — 208px, measured at 210 with borders, so
 * 114px around the content. 120 leaves a little room for antd's defaults to
 * move without re-opening this, while still rejecting anything that goes back
 * to piling product-specific padding on top of them.
 */
test.describe("空状态卡片不为一张小图占掉一屏的一半", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  for (const tab of ["成片", "制作任务", "预览"] as const) {
    test(`「${tab}」页签的空状态卡片贴合其内容`, async ({ page }) => {
      await openVideoStudio(page);
      await page.getByRole("tab", { name: tab }).click();

      const measured = await page.locator(".desktop-content main").evaluate((main) => {
        const card = Array.from(main.querySelectorAll(".video-studio-panel")).find(
          (candidate) => candidate.getBoundingClientRect().height > 0,
        );
        if (card === undefined) return null;
        const empty = card.querySelector(".ant-empty");
        if (empty === null) return null;
        return {
          card: Math.round(card.getBoundingClientRect().height),
          content: Math.round(empty.getBoundingClientRect().height),
        };
      });

      expect(measured, `「${tab}」页签上没有找到空状态卡片`).not.toBeNull();
      expect(
        measured!.card - measured!.content,
        `「${tab}」空状态卡片 ${measured!.card}px，里面只有 ${measured!.content}px 内容，` +
          `空掉 ${measured!.card - measured!.content}px`,
      ).toBeLessThanOrEqual(120);
    });
  }
});
