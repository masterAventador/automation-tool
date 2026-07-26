import { expect, test } from "@playwright/test";

/**
 * Picking a creation method has to put the thing it unlocked on screen.
 *
 * Measured on 2026-07-26 against the shipped layout: the two method cards sit
 * at the very bottom of a 1854px page, so the operator has already scrolled to
 * reach them. Choosing 品牌动效成片 inserts the one-sentence card *above* that
 * scroll position — content height grew by 485px and the browser's scroll
 * anchoring moved the viewport down by exactly as much to keep the card the
 * user was looking at still. The one-sentence textarea landed at y = -412,
 * entirely above the viewport. The only change on screen was a tag flipping
 * from 可选择 to 已选择.
 *
 * That is the first step of the demo — "输一句话" — and pressing the button
 * that is supposed to start it appears to do nothing at all.
 *
 * The assertions deliberately use `toBeInViewport` rather than a scroll offset:
 * the shell scrolls `.desktop-content`, not the document (see
 * `shell-layout.spec.ts`), so any assertion written against `window.scrollY`
 * would pass while the user still saw nothing.
 */

test.beforeEach(async ({ page }) => {
  await page.goto("/harness.html?health=available");
  await page.getByRole("menuitem", { name: "视频制作" }).click();
  await expect(page.getByRole("tab", { name: "新建视频" })).toBeVisible();
});

test("choosing brand motion brings the one-sentence entry into view", async ({
  page,
}) => {
  const brief = page.getByLabel("一句话视频需求");
  await expect(brief).toBeHidden();

  // Auto-scrolling to the button before clicking it is exactly what the
  // operator does: the method cards are below the fold.
  await page.getByRole("button", { name: "选择品牌动效成片" }).click();

  await expect(brief).toBeInViewport({ ratio: 1 });
  await expect(page.getByRole("button", { name: "开始自动制作" })).toBeInViewport();
});
