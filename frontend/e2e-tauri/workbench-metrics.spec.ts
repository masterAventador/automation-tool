import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

interface WorkbenchMetricsPreparation {
  readonly installationId: string;
  readonly taskId: string;
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

async function metricValue(title: string): Promise<string | null> {
  return browser.execute((target) => {
    for (const metric of Array.from(document.querySelectorAll(".ant-statistic"))) {
      if (metric.querySelector(".ant-statistic-title")?.textContent?.trim() === target) {
        return metric.querySelector(".ant-statistic-content-value")?.textContent?.trim() ?? null;
      }
    }
    return null;
  }, title);
}

describe("H8-14 workbench metrics production-path acceptance", () => {
  it("renders Installation-scoped PostgreSQL facts through the hidden App", async () => {
    await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");
    const preparation = (await browser.tauri.execute(({ core }) =>
      core.invoke("prepare_workbench_metrics_for_acceptance"),
    )) as WorkbenchMetricsPreparation;
    assert.match(preparation.installationId, UUID_V4);
    assert.match(preparation.taskId, UUID_V4);

    await browser.waitUntil(
      async () => {
        const retry = await browser.$("button=重新加载工作台");
        return (await retry.isExisting()) || (await browser.$("body").getText()).includes(preparation.taskId);
      },
      { timeout: 30_000, timeoutMsg: "workbench did not expose a reload or refreshed task" },
    );
    const retry = await browser.$("button=重新加载工作台");
    if (await retry.isExisting()) {
      await retry.click();
    }
    const expected: ReadonlyArray<readonly [string, string]> = [
      ["累计任务", "8"],
      ["成功任务", "2"],
      ["失败任务", "1"],
      ["当前需接管", "1"],
      ["任务结果待确认", "1"],
      ["成功动作", "1"],
      ["失败动作", "1"],
      ["动作结果待确认", "1"],
    ];
    await browser.waitUntil(
      async () => {
        for (const [title, value] of expected) {
          if ((await metricValue(title)) !== value) return false;
        }
        return true;
      },
      { timeout: 90_000, timeoutMsg: "workbench metrics did not reach seeded facts" },
    );

    const bodyText = await browser.$("body").getText();
    assert.ok(bodyText.includes("累计任务"));
    assert.ok(bodyText.includes("Control Plane 已连接"));
    assert.ok(bodyText.includes("Executor 离线"));
    assert.doesNotMatch(
      bodyText,
      /Cookie|comment_body|message_body|executor-ledger|\/Users\/|[A-Z]:\\/i,
    );
  });
});
