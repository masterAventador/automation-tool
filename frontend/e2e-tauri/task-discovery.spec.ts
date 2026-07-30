import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";
import {
  openAutomationRuns,
  waitForStartup,
  waitForTaskRow,
} from "./navigation";

interface TaskDiscoveryPreparation {
  readonly installationId: string;
  readonly taskId: string;
  readonly competingTaskId: string;
  readonly taskStatus: string;
  readonly taskRevision: number;
  readonly lastEventSequence: number;
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("Task discovery production-path acceptance", () => {
  it("converges candidates through the hidden real App and formal Executor", async () => {
    await waitForStartup();

    const preparation = (await browser.tauri.execute(({ core }) =>
      core.invoke("prepare_task_discovery_for_acceptance"),
    )) as TaskDiscoveryPreparation;

    assert.match(preparation.installationId, UUID_V4);
    assert.match(preparation.taskId, UUID_V4);
    assert.match(preparation.competingTaskId, UUID_V4);
    assert.notEqual(preparation.competingTaskId, preparation.taskId);
    assert.equal(preparation.taskStatus, "draft");
    assert.equal(preparation.taskRevision, 1);
    assert.equal(preparation.lastEventSequence, 0);

    await browser.refresh();
    // 改版把默认落地页换成了 AI 助理，所以刷新之后停的不再是运行记录。
    await waitForStartup();
    await openAutomationRuns();
    // 每次 refresh 之后重新取一次：刷新会让先前解析到的元素引用作废。
    let body = await browser.$("body");
    await waitForTaskRow(preparation.taskId);
    await waitForTaskRow(preparation.competingTaskId);
    await browser.$(`button[data-task-id="${preparation.taskId}"]`).click();
    await expect(await browser.$("h3=任务运行详情")).toExist();
    await browser.$("button=开始目标发现").click();
    await browser.waitUntil(
      async () => (await body.getText()).includes("发现目标中"),
      { timeout: 10_000, timeoutMsg: "First UI-started Task did not enter discovery" },
    );

    await browser.$("button=返回工作台").click();
    await openAutomationRuns();
    // 另两处都等了，这里漏了：列表要等它自己渲染出来，点击不会替你等。
    await waitForTaskRow(preparation.competingTaskId);
    await browser.$(`button[data-task-id="${preparation.competingTaskId}"]`).click();
    await expect(await browser.$("h3=任务运行详情")).toExist();
    await browser.$("button=开始目标发现").click();
    try {
      await browser.waitUntil(
        async () => (await body.getText()).includes("当前设备已有任务正在运行"),
        { timeout: 10_000 },
      );
    } catch {
      throw new Error(
        `Competing Task did not show Installation busy: ${await body.getText()}`,
      );
    }
    await browser.tauri.execute(({ core }) =>
      core.invoke("signal_task_discovery_busy_for_acceptance"),
    );

    await browser.refresh();
    await waitForStartup();
    await openAutomationRuns();
    body = await browser.$("body");
    await waitForTaskRow(preparation.taskId);
    await browser.$(`button[data-task-id="${preparation.taskId}"]`).click();
    await browser.waitUntil(
      async () => (await body.getText()).includes(preparation.taskId),
      { timeout: 10_000, timeoutMsg: "Task details did not open the first Task" },
    );
    try {
      await browser.waitUntil(
        async () => {
          const text = await body.getText();
          return (
            text.includes("等待确认") &&
            text.includes("目标预览") &&
            text.includes("验收目标 1") &&
            text.includes("验收目标 2")
          );
        },
        { timeout: 30_000 },
      );
    } catch {
      throw new Error(`UI-started discovery did not converge to preview: ${await body.getText()}`);
    }
    const text = await body.getText();
    assert.doesNotMatch(text, /acceptance-author-|产品登录|注册账号|账号登录/);

    // The acceptance subject includes App-owned sidecar lifecycle: let the App
    // close the signed Executor before WebdriverIO tears down its session.
    await browser.tauri.execute(({ core }) => core.invoke("exit_app_for_acceptance"));
  });
});
