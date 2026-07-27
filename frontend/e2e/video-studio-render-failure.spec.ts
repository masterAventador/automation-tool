import { expect, test } from "@playwright/test";
import { openVideoStudio } from "./navigation";

/**
 * The render dies while the operator is on another page.
 *
 * `video-studio-one-sentence.spec.ts` covers the half T91 fixed: the failure
 * arrives from the submit call itself, so it lands in the store no matter which
 * page is mounted. This is the half T91 registered and left open. Here the
 * submission *succeeds* — authoring returns, a real render job starts, the App
 * says 本机渲染开始了 — and only then does the render fail. Nothing used to look
 * at that job unless the studio page happened to be mounted, so the sidebar
 * went on showing a blue dot whose hover text read 视频制作正在进行中 over a
 * film that was already over.
 *
 * The whole point is that the operator never comes back to look, so the spec
 * asserts the workbench heading is still on screen at the moment the sidebar
 * admits the failure. Whatever the sidebar says there, it says without the
 * studio page having been mounted to say it.
 */
test("a render that fails while the operator is elsewhere still says so", async ({
  page,
}) => {
  await page.goto("/harness.html?health=available&scenario=motion-render-failure");

  await openVideoStudio(page);
  await expect(page.getByRole("tab", { name: "新建视频" })).toBeVisible();
  await page.getByRole("button", { name: "选择品牌动效成片" }).click();
  await page.getByLabel("一句话视频需求").fill("用蓝色商务风做一段本周销售增长说明");
  await page.getByRole("button", { name: "开始自动制作" }).click();

  // 编排回来了，本机渲染真的开始了——到这一步为止一切正常。
  await expect(page.getByText(/本机渲染开始了/)).toBeVisible({ timeout: 15_000 });

  const videoEntry = page.getByRole("menuitem", { name: /创作/ });
  await page.getByRole("menuitem", { name: "AI 助理" }).click();
  const workbench = page.getByRole("heading", { name: "AI 运营助理" });
  await expect(workbench).toBeVisible();

  // 渲染在这之后才失败，而制作页从头到尾没有被挂载过。
  await expect(videoEntry.getByText("失败")).toBeVisible({ timeout: 20_000 });
  await expect(videoEntry.locator("[title='视频制作正在进行中']")).toHaveCount(0);
  await expect(workbench).toBeVisible();

  // 回到那一页，原因在任务卡上等着。
  await openVideoStudio(page);
  await expect(page.getByText(/本机渲染未完成/)).toBeVisible();
});
