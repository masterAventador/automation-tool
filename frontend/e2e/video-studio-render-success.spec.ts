import { expect, test } from "@playwright/test";
import { openVideoStudio } from "./navigation";

/**
 * 成片做好了，而用户在别的页面。
 *
 * `video-studio-render-failure.spec.ts` 覆盖的是这条链路的坏消息一半：渲染在用户切页
 * 之后失败，侧边栏必须说出来。T91b 在自己的记录里登记了同族的另一半——好消息没有送到。
 * 监视器内部已经把这条任务标成结束了，可屏幕上和以前一模一样：蓝点，加一句
 * `title="视频制作正在进行中"`。对一条已经做完的片子那句话是假的，它把用户按在原地
 * 等一件早就发生完的事。
 *
 * 整条用例的前提是用户再也没有回去看，所以侧边栏说出「完成」的那一刻，断言工作台的
 * 标题仍然在屏幕上——那一句是在制作页从头到尾没有被挂载过的情况下说出来的。
 */
test("a render that finishes while the operator is elsewhere says so too", async ({
  page,
}) => {
  await page.goto("/harness.html?health=available&scenario=motion-render-success");

  await openVideoStudio(page);
  await expect(page.getByRole("tab", { name: "新建视频" })).toBeVisible();
  await page.getByRole("button", { name: "选择品牌动效成片" }).click();
  await page.getByLabel("一句话视频需求").fill("用蓝色商务风做一段本周销售增长说明");
  await page.getByRole("button", { name: "开始自动制作" }).click();

  // 编排回来了，本机渲染真的开始了。
  await expect(page.getByText(/本机渲染开始了/)).toBeVisible({ timeout: 15_000 });

  const videoEntry = page.getByRole("menuitem", { name: /创作/ });
  await page.getByRole("menuitem", { name: "AI 助理" }).click();
  const workbench = page.getByRole("heading", { name: "AI 运营助理" });
  await expect(workbench).toBeVisible();

  // 渲染在这之后才做完，而制作页从头到尾没有被挂载过。
  await expect(videoEntry.getByText("完成")).toBeVisible({ timeout: 20_000 });
  await expect(videoEntry.locator("[title='视频制作正在进行中']")).toHaveCount(0);
  await expect(workbench).toBeVisible();

  // 回到那一页，成片和那个按钮在等着。
  await openVideoStudio(page);
  await expect(page.getByText(/已经做好了/)).toBeVisible();

  // 看过了就不该再提醒——「去看成片」是唯一那次确认，标记跟着它一起灭掉。
  await page.getByRole("button", { name: "去看成片" }).click();
  await expect(videoEntry.getByText("完成")).toHaveCount(0);
});
