import { expect, test } from "@playwright/test";

/**
 * 用户自己按下取消，然后走开。
 *
 * `video-studio-render-failure.spec.ts` 和 `video-studio-render-success.spec.ts`
 * 覆盖了自己到达的两种终态。第三种终态是用户按出来的，而它一直没被接上：监视器把任务
 * 记成 `cancelled`、定时器也停了，侧边栏却还挂着蓝点和一句 `title="视频制作正在进行中"`。
 * 那条运行已经不存在了，标记还在替它说话——本条线从 T91 起一直在修的同一个病。
 *
 * 成因不在取消这条链路上，在判据上：`motionRunAttention` 的 `running` 分支认的是
 * `settleMotionRun` 写下的那条 info 提示还在不在，而那是**页面上的一条通告**，不是
 * 「还有没有东西在跑」的答案。
 *
 * 整条用例走真实用户路径：选制作方式 → 一句话 → 提交 → 编排返回、本机渲染真的开始 →
 * 在任务卡上按「取消任务」并确认 → 切到工作台。断言侧边栏一个角标都没有，而不只是
 * 「没有蓝点」——把取消顺手归进别的分支会让屏幕上冒出「完成」或「失败」，那比蓝点更糟。
 */
test("a run the operator cancelled stops being called progress", async ({ page }) => {
  await page.goto("/harness.html?health=available&scenario=motion-render-cancel");

  await page.getByRole("menuitem", { name: "视频制作" }).click();
  await expect(page.getByRole("tab", { name: "新建视频" })).toBeVisible();
  await page.getByRole("button", { name: "选择品牌动效成片" }).click();
  await page.getByLabel("一句话视频需求").fill("用蓝色商务风做一段本周销售增长说明");
  await page.getByRole("button", { name: "开始自动制作" }).click();

  // 编排回来了，本机渲染真的开始了——此刻蓝点是属实的。
  await expect(page.getByText(/本机渲染开始了/)).toBeVisible({ timeout: 15_000 });
  const videoEntry = page.getByRole("menuitem", { name: /视频制作/ });
  await expect(videoEntry.locator("[title='视频制作正在进行中']")).toBeVisible();

  // 用户按下取消并确认。
  await page.getByRole("button", { name: "取消品牌动效任务" }).click();
  // 组件库给两个字的按钮加了字距，可访问名是「确 定」，所以按已有用例的写法留出空白。
  await page.getByRole("button", { name: /确\s*定/ }).click();
  await expect(page.getByText("已取消")).toBeVisible({ timeout: 15_000 });

  await page.getByRole("menuitem", { name: "工作台" }).click();
  const workbench = page.getByRole("heading", { name: "RPA 运营工作台" });
  await expect(workbench).toBeVisible();

  // 那条运行已经不存在了，侧边栏也就没什么可说的。
  await expect(videoEntry.locator("[title='视频制作正在进行中']")).toHaveCount(0, {
    timeout: 20_000,
  });
  await expect(videoEntry.locator("sup")).toHaveCount(0);
  await expect(workbench).toBeVisible();
});
