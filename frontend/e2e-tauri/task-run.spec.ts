import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

interface TaskRunPreparation {
  readonly installationId: string;
  readonly controlledTaskId: string;
  readonly emergencyTaskId: string;
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

async function waitForRenderedText(...expected: string[]): Promise<void> {
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
        timeoutMsg: `Task run page did not render: ${expected.join(", ")}`,
      },
    );
  } catch {
    throw new Error(`Latest Task run text: ${latestText}`);
  }
}

async function openTask(taskId: string): Promise<void> {
  await browser.$(`button=${taskId}`).click();
  await waitForRenderedText("任务运行详情", taskId, "任务开始", "步骤开始");
}

async function clickTwoCharacterButton(
  first: string,
  second: string,
): Promise<void> {
  await browser
    .$(`//button[contains(., '${first}') and contains(., '${second}')]`)
    .click();
}

describe("Task run production-path acceptance", () => {
  it("renders persisted history and controls two Tasks from the hidden App UI", async () => {
    const heading = await browser.$("h2");
    await expect(heading).toHaveText("RPA 运营工作台");

    const preparation = (await browser.tauri.execute(({ core }) =>
      core.invoke("prepare_task_run_for_acceptance"),
    )) as TaskRunPreparation;
    assert.match(preparation.installationId, UUID_V4);
    assert.match(preparation.controlledTaskId, UUID_V4);
    assert.match(preparation.emergencyTaskId, UUID_V4);

    const retry = await browser.$("button=重新加载工作台");
    await browser.waitUntil(
      async () => {
        const bodyText = await browser.$("body").getText();
        return (
          (await retry.isExisting()) ||
          (bodyText.includes(preparation.controlledTaskId) &&
            bodyText.includes(preparation.emergencyTaskId))
        );
      },
      { timeout: 90_000, timeoutMsg: "workbench did not expose Task run fixtures" },
    );
    if (await retry.isExisting()) await retry.click();

    await waitForRenderedText(
      preparation.controlledTaskId,
      preparation.emergencyTaskId,
      "Executor 在线",
      "运行中",
    );

    await openTask(preparation.controlledTaskId);
    await clickTwoCharacterButton("暂", "停");
    await waitForRenderedText("暂停命令已提交", "已暂停", "任务已暂停");
    await clickTwoCharacterButton("恢", "复");
    await waitForRenderedText("恢复命令已提交", "运行中", "任务已恢复");
    await browser.$("button=取消任务").click();
    await browser.$("button=确认取消").click();
    await waitForRenderedText("取消命令已提交", "已取消", "任务已取消");

    await browser.$("button=返回工作台").click();
    await waitForRenderedText("RPA 运营工作台", preparation.emergencyTaskId);
    await openTask(preparation.emergencyTaskId);
    await browser.$("button=紧急停止").click();
    await browser.$("button=确认紧停").click();
    await waitForRenderedText("紧停命令已提交", "结果待确认");
  });
});
