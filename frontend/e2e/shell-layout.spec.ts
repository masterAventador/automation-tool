import { expect, test } from "@playwright/test";

/**
 * The left navigation must stay put while the content scrolls.
 *
 * Reported from real use on 2026-07-26: "整个页面现在是一起上下滚动的… 我想切换
 * 菜单都需要来回的滚动". The shell pinned itself with `min-height: 100vh`, so a
 * tall page grew the whole shell and the document scrolled — taking the sidebar
 * with it. `.desktop-content` already declared `overflow: auto`, but no ancestor
 * constrained its height, so it could never overflow and that rule never fired.
 *
 * The assertions are on the two halves of that: the document itself must not
 * scroll, and the content region must.
 */

const TALL_PAGE = "设置";

test.beforeEach(async ({ page }) => {
  await page.goto("/harness.html?health=available&scenario=publishing");
  await page.getByRole("menuitem", { name: TALL_PAGE }).click();
  await expect(page.getByRole("menuitem", { name: TALL_PAGE })).toBeVisible();
});

test("the document itself never scrolls", async ({ page }) => {
  const overflow = await page.evaluate(() => {
    const root = document.documentElement;
    return root.scrollHeight - root.clientHeight;
  });

  expect(overflow).toBeLessThanOrEqual(1);
});

test("the content region is the thing that scrolls", async ({ page }) => {
  const content = page.locator(".desktop-content");

  const scrollable = await content.evaluate(
    (element) => element.scrollHeight > element.clientHeight,
  );

  expect(scrollable).toBe(true);
});

test("scrolling the content leaves the navigation where it was", async ({
  page,
}) => {
  const item = page.getByRole("menuitem", { name: TALL_PAGE });
  const before = await item.boundingBox();

  await page.locator(".desktop-content").evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });

  const after = await item.boundingBox();

  expect(before).not.toBeNull();
  expect(after).not.toBeNull();
  expect(after!.y).toBeCloseTo(before!.y, 0);
  await expect(item).toBeInViewport();
});
