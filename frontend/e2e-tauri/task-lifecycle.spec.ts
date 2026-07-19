import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

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

async function openCreatePage(): Promise<void> {
  await browser
    .$("//li[contains(@class,'ant-menu-item') and .//*[normalize-space()='新建任务']]")
    .click();
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
    await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");
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
    await waitForRenderedText("RPA 运营工作台", controlledTaskId);
    await openCreatePage();
    const succeededTaskId = await createTask("T3-19 成功链路");
    await waitForRenderedText("已成功", "任务完成", "100%");

    await browser.refresh();
    await waitForRenderedText(
      "RPA 运营工作台",
      controlledTaskId,
      succeededTaskId,
      "已取消",
      "已成功",
    );
    await browser.$(`button=${succeededTaskId}`).click();
    await waitForRenderedText("任务运行详情", succeededTaskId, "任务完成", "100%");
    assert.equal(/产品登录|注册账号|账号登录/.test(await browser.$("body").getText()), false);
  });
});
