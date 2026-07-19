import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

interface TaskCreateFormPreparation {
  readonly installationId: string;
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("Task form production-path acceptance", () => {
  it("validates and creates one Unicode-boundary Task from the hidden real App UI", async () => {
    await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");
    const preparation = (await browser.tauri.execute(({ core }) =>
      core.invoke("prepare_task_create_form_for_acceptance"),
    )) as TaskCreateFormPreparation;
    assert.match(preparation.installationId, UUID_V4);

    const newTaskMenu = await browser.$(
      "//li[contains(@class,'ant-menu-item') and .//*[normalize-space()='新建任务']]",
    );
    await newTaskMenu.click();
    await expect(await browser.$("h2")).toHaveText("新建运营任务");

    const keyword = await browser.$("#searchKeyword");
    await keyword.setValue("control\u0085character");
    await browser.$("button=创建任务").click();

    const body = await browser.$("body");
    await browser.waitUntil(
      async () => (await body.getText()).includes("请输入有效的搜索关键词"),
      { timeout: 20_000, timeoutMsg: "Task form accepted a control character" },
    );

    await keyword.setValue("😀".repeat(80));
    await browser.$("#targetLimit").setValue("100");
    await browser.$("button=创建任务").click();

    await browser.waitUntil(
      async () => (await body.getText()).includes("任务已创建："),
      { timeout: 60_000, timeoutMsg: "Task form did not expose its creation receipt" },
    );
    const text = await body.getText();
    const taskId = text.match(/任务已创建：([0-9a-f-]{36})/)?.[1];
    assert.match(taskId ?? "", UUID_V4);
    assert.equal(/产品登录|注册账号|账号登录/.test(text), false);
  });
});
