import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";
import {
  openAutomationRuns,
  openTaskCreate,
  waitForStartup,
} from "./navigation";

interface TaskLifecyclePreparation {
  readonly installationId: string;
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

async function waitForRenderedText(...expected: string[]): Promise<string> {
  const body = await browser.$("body");
  let latestText = "";
  try {
    await browser.waitUntil(
      async () => {
        latestText = await body.getText();
        return expected.every((value) => latestText.includes(value));
      },
      {
        timeout: 90_000,
        timeoutMsg: `Lifecycle page did not render: ${expected.join(", ")}`,
      },
    );
  } catch {
    throw new Error(`Latest lifecycle text: ${latestText}`);
  }
  return latestText;
}

/** 等某一条任务出现在列表里，按它的标识而不是按它的显示名。 */
async function waitForTaskRow(taskId: string): Promise<void> {
  await browser.waitUntil(
    async () => browser.$(`button[data-task-id="${taskId}"]`).isExisting(),
    { timeout: 90_000, timeoutMsg: `运行记录里没有出现任务 ${taskId}` },
  );
}

async function openCreatePage(): Promise<void> {
  await openTaskCreate();
  await expect(await browser.$("h2")).toHaveText("新建运营任务");
}

async function createTask(keyword: string): Promise<string> {
  await browser.$("#searchKeyword").setValue(keyword);
  await browser.$("#targetLimit").setValue("3");
  await browser.$("button=创建任务").click();
  const text = await waitForRenderedText("任务已创建：", "查看运行详情");
  const taskId = text.match(/任务已创建：([0-9a-f-]{36})/)?.[1];
  assert.match(taskId ?? "", UUID_V4);
  await browser.$("button=查看运行详情").click();
  await waitForRenderedText("任务运行详情", taskId ?? "");
  return taskId!;
}

async function clickTwoCharacterButton(first: string, second: string): Promise<void> {
  await browser
    .$(`//button[contains(., '${first}') and contains(., '${second}')]`)
    .click();
}

describe("T3-19 hidden App lifecycle acceptance", () => {
  it("creates, controls, succeeds, refreshes, and restores persisted Tasks", async () => {
    await waitForStartup();
    const preparation = (await browser.tauri.execute(({ core }) =>
      core.invoke("prepare_task_lifecycle_for_acceptance"),
    )) as TaskLifecyclePreparation;
    assert.match(preparation.installationId, UUID_V4);

    await openCreatePage();
    const controlledTaskId = await createTask("T3-19 取消链路");
    await waitForRenderedText("运行中", "任务开始", "步骤开始");
    await clickTwoCharacterButton("暂", "停");
    await waitForRenderedText("暂停命令已提交", "已暂停", "任务已暂停");
    await clickTwoCharacterButton("恢", "复");
    await waitForRenderedText("恢复命令已提交", "运行中", "任务已恢复");
    await browser.$("button=取消任务").click();
    await browser.$("button=确认取消").click();
    await waitForRenderedText("取消命令已提交", "已取消", "任务已取消");

    await browser.$("button=返回工作台").click();
    // 「返回工作台」把页面切到自动化中心而不是运行记录列表。
    await openAutomationRuns();
    // 这一处保持按文本等：首轮实测它是通过的，说明这个阶段 UUID 确实在页面上。
    // 只有刷新之后那一处才需要按标识找行（那时列表里只剩时间戳标签）。
    await waitForRenderedText(controlledTaskId);
    await openCreatePage();
    const succeededTaskId = await createTask("T3-19 成功链路");
    await waitForRenderedText("已成功", "任务完成", "100%");

    await browser.refresh();
    // 改版把默认落地页换成了 AI 助理，所以刷新之后停的不再是运行记录。
    // 这一段要证的是「任务在重新加载之后仍然在」，不是「重新加载之后还停在原页」
    // ——所以先按用户会走的路导航回去，再断言持久化。
    await waitForStartup();
    await openAutomationRuns();
    // 列表里的行现在按创建时刻命名（`07-29 12:01:54 的任务`），不再印 UUID
    // ——那是有意的可读性改动。任务标识仍在，作为惰性的 `data-task-id`，
    // 所以这里改成按标识找行，再顺带断言两条的终态都还在。
    await waitForTaskRow(controlledTaskId);
    await waitForTaskRow(succeededTaskId);
    await waitForRenderedText("已取消", "已成功");
    await browser.$(`button[data-task-id="${succeededTaskId}"]`).click();
    await waitForRenderedText("任务运行详情", succeededTaskId, "任务完成", "100%");
    assert.equal(/产品登录|注册账号|账号登录/.test(await browser.$("body").getText()), false);
  });
});
