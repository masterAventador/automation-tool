import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

interface TargetPreviewUiPreparation {
  readonly installationId: string;
  readonly taskId: string;
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("Task target preview UI production-path acceptance", () => {
  it("loads, excludes, and confirms from the hidden real App UI", async () => {
    await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");
    const preparation = (await browser.tauri.execute(({ core }) =>
      core.invoke("prepare_task_target_preview_ui_for_acceptance"),
    )) as TargetPreviewUiPreparation;
    assert.match(preparation.installationId, UUID_V4);
    assert.match(preparation.taskId, UUID_V4);

    await browser.refresh();
    const body = await browser.$("body");
    await browser.waitUntil(
      async () => (await body.getText()).includes("等待确认"),
      { timeout: 60_000, timeoutMsg: "Workbench did not load the prepared Task" },
    );
    await browser.$("button=查看运行详情").click();
    await expect(await browser.$("h3=任务运行详情")).toExist();
    await expect(await browser.$("h4=目标预览")).toExist();

    const secondTarget = await browser.$(
      "input[aria-label='选择目标 验收目标 2']",
    );
    assert.equal(await secondTarget.isSelected(), true);
    await secondTarget.click();
    await browser.waitUntil(
      async () => (await body.getText()).includes("本次排除 1 个"),
      { timeout: 60_000, timeoutMsg: "Target exclusion did not reach the UI" },
    );
    assert.equal(await secondTarget.isSelected(), false);

    await browser.$("button=确认执行").click();
    await browser.$("button=确认目标").click();
    await browser.waitUntil(
      async () => (await body.getText()).includes("目标已确认，任务已进入执行队列"),
      { timeout: 60_000, timeoutMsg: "Target confirmation did not reach the UI" },
    );
    assert.equal(await browser.$("button=确认执行").isEnabled(), false);

    const text = await body.getText();
    assert.match(text, /已发现 2 个目标/);
    assert.match(text, /计划执行 1 个/);
    assert.match(text, /抖音通用搜索作者/);
    assert.doesNotMatch(text, /acceptance-author-|产品登录|注册账号|账号登录/);
  });
});
