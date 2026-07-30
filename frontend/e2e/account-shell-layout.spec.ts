import { expect, test } from "@playwright/test";

import { MINIMUM_WINDOW, PRODUCTION_WINDOW } from "./production-window";

/**
 * The shell still has to fit the viewport once an account bar sits above it.
 *
 * Reported from the signed customer Demo package on 2026-07-27: "左侧菜单还是会
 * 跟着右侧内容一起滚动", with the composer cut off below the window edge.
 *
 * `e2e/shell-layout.spec.ts` asserted exactly this and stayed green, because the
 * harness mounted `WorkbenchShell` on its own while the customer Demo profile
 * wraps it in `AccountSessionGate`. That gate adds a ~68px bar *above* a shell
 * which claims `height: 100vh` for itself, so the document is 68px taller than
 * the window and the whole page — navigation included — scrolls.
 *
 * The lesson is the one this project keeps paying for: a layout assertion is
 * only worth what the shape it measures is worth. So these run against
 * `?account=signed-in`, the shape a customer actually opens.
 */

const TALL_PAGE = "自动化";

for (const [label, size] of [
  ["生产窗口", PRODUCTION_WINDOW],
  ["最小窗口", MINIMUM_WINDOW],
] as const) {
  test.describe(`${label}，已登录产品账号`, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize(size);
      await page.goto("/harness.html?health=available&account=signed-in");
      await expect(page.locator(".account-session-bar")).toBeVisible();
    });

    test("文档本身不滚动", async ({ page }) => {
      const overflow = await page.evaluate(() => {
        const root = document.documentElement;
        return root.scrollHeight - root.clientHeight;
      });

      expect(overflow).toBeLessThanOrEqual(1);
    });

    test("滚动内容之后左侧导航仍在原处", async ({ page }) => {
      await page.getByRole("menuitem", { name: TALL_PAGE }).click();
      const navigation = page.getByRole("menuitem", { name: TALL_PAGE });
      const before = await navigation.boundingBox();

      await page.locator(".desktop-content").evaluate((element) => {
        element.scrollTop = element.scrollHeight;
      });
      // A scroll the document performed instead would move the bar too.
      await page.mouse.wheel(0, 600);
      const after = await navigation.boundingBox();

      expect(after?.y).toBeCloseTo(before?.y ?? -1, 0);
      expect(after?.y ?? -1).toBeGreaterThanOrEqual(0);
    });

    test("AI 助理的输入框整个在视口之内", async ({ page }) => {
      const composer = page.locator(".assistant-composer textarea").first();
      await expect(composer).toBeVisible();

      const fits = await composer.evaluate(
        (element) => element.getBoundingClientRect().bottom <= window.innerHeight + 1,
      );

      expect(fits).toBe(true);
    });
  });
}
