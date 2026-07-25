import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

interface WorkbenchPreparation {
  readonly installationId: string;
  readonly taskId: string;
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
        timeout: 60_000,
        timeoutMsg: `workbench did not render: ${expected.join(", ")}`,
      },
    );
  } catch {
    throw new Error(`Latest workbench text: ${latestText}`);
  }
}

describe("Workbench production-path acceptance", () => {
  it("loads real projections and emergency-stops from the hidden App UI", async () => {
    const heading = await browser.$("h2");
    await expect(heading).toHaveText("RPA 运营工作台");

    const preparation = (await browser.tauri.execute(({ core }) =>
      core.invoke("prepare_workbench_for_acceptance"),
    )) as WorkbenchPreparation;
    assert.match(preparation.installationId, UUID_V4);
    assert.match(preparation.taskId, UUID_V4);

    const retry = await browser.$("button=重新加载工作台");
    await browser.waitUntil(
      async () => {
        const bodyText = await browser.$("body").getText();
        return (await retry.isExisting()) || bodyText.includes(preparation.taskId);
      },
      { timeout: 60_000, timeoutMsg: "workbench did not expose a reload or Task state" },
    );
    if (await retry.isExisting()) {
      await retry.click();
    }

    await waitForRenderedText(
      preparation.taskId,
      "控制服务已连接",
      "本机执行器在线",
      "运行中",
    );

    await browser.$("button=全局紧急停止").click();
    await browser.$("button=确认紧停").click();

    await waitForRenderedText("紧停命令已提交", "结果待确认");
    assert.equal(await browser.$("button=全局紧急停止").isEnabled(), false);
  });
});
