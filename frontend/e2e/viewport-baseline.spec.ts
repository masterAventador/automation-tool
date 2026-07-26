import { expect, test } from "@playwright/test";
import { PRODUCTION_WINDOW } from "./production-window";

/**
 * The viewport every other spec in this folder inherits has to be the window
 * the product actually opens at.
 *
 * This is the guard for T96. `playwright.config.ts` declared
 * `viewport: {1280, 800}` at the top level while its one project spread
 * `devices["Desktop Chrome"]`; a project-level `use` wins over the top level,
 * and that preset carries its own 1280x720. So the declared height lost, and
 * every spec here ran 80px shorter than its own file header claimed —
 * `screen-density.spec.ts` said "1280x800, the production window" in its first
 * paragraph and measured 720.
 *
 * The deviation was in the strict direction (a shorter viewport puts the fold
 * higher, so "below the fold" was easier to prove), which is why it hid: no
 * assertion ever went red over it. Nothing was wrong with what those specs
 * caught — only with the number they said they caught it at.
 *
 * Two assertions rather than one, because they can disagree. The first is what
 * Playwright believes it handed the page; the second is what layout code inside
 * the page actually measures — `window.innerHeight` is the literal fold in
 * `video-studio-density.spec.ts`, and a classic scrollbar would make
 * `innerWidth` narrower than the viewport it was given.
 */

test("每个用例继承到的视窗就是生产窗口的尺寸", async ({ page }) => {
  expect(
    page.viewportSize(),
    "共享配置发下来的视窗尺寸和 tauri.conf.json 声明的生产窗口对不上",
  ).toEqual(PRODUCTION_WINDOW);
});

test("页面里量到的折叠线就是生产窗口的高度", async ({ page }) => {
  await page.goto("/harness.html?health=available");

  const measured = await page.evaluate(() => ({
    width: window.innerWidth,
    height: window.innerHeight,
  }));

  expect(
    measured,
    "页面内量到的可视区域和 tauri.conf.json 声明的生产窗口对不上",
  ).toEqual(PRODUCTION_WINDOW);
});
