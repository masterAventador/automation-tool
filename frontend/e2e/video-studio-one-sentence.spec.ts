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

/**
 * Making a film takes minutes, so the operator leaves the page — and until now
 * that erased every trace of it.
 *
 * Measured against the real App on 2026-07-26: submit, leave for 75 seconds,
 * come back, and the jobs list read 还没有真实制作任务, the sentence was gone,
 * the chosen method was gone. Worse, the submission's result was written into a
 * component the shell had already unmounted, so a run that failed while the
 * operator was elsewhere failed silently and its reason was lost for good.
 *
 * This walks it through the sidebar, the way the operator does. The harness
 * gives the studio the shell's own gateway, which refuses the submission — so
 * what is being checked is precisely a result that arrives and then has to
 * survive a page change.
 */
test("a submission and its result survive leaving the page", async ({ page }) => {
  await page.getByRole("button", { name: "选择品牌动效成片" }).click();
  await page.getByLabel("一句话视频需求").fill("用蓝色商务风做一段本周销售增长说明");
  await page.getByRole("button", { name: "开始自动制作" }).click();

  const failure = page.getByText(/暂时无法提交|暂时不可用|自动编排/);
  await expect(failure).toBeVisible();

  const videoEntry = page.getByRole("menuitem", { name: "视频制作" });
  await page.getByRole("menuitem", { name: "工作台" }).click();
  await expect(page.getByRole("heading", { name: "RPA 运营工作台" })).toBeVisible();

  // Away from the page, the sidebar is the only thing that can still say so —
  // and this submission failed, so what it says has to be that. It used to
  // read 视频制作正在进行中 for a run that was already over, which is how a
  // failure stayed invisible for twelve minutes in the 2026-07-26 injection.
  await expect(videoEntry.getByText("失败")).toBeVisible();
  await expect(videoEntry.locator("[title='视频制作正在进行中']")).toHaveCount(0);

  await videoEntry.click();
  await expect(page.getByText(/暂时无法提交|暂时不可用|自动编排/)).toBeVisible();

  await page.getByRole("tab", { name: "新建视频" }).click();
  await expect(page.getByLabel("一句话视频需求")).toHaveValue(
    "用蓝色商务风做一段本周销售增长说明",
  );
});
