import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

interface ExecutorLifecyclePreparation {
  readonly installationId: string;
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

async function waitForText(...expected: string[]): Promise<string> {
  const body = await browser.$("body");
  let latest = "";
  try {
    await browser.waitUntil(
      async () => {
        latest = await body.getText();
        return expected.every((value) => latest.includes(value));
      },
      {
        timeout: 90_000,
        timeoutMsg: `Executor lifecycle page did not render: ${expected.join(", ")}`,
      },
    );
  } catch {
    throw new Error(`Latest Executor lifecycle text: ${latest}`);
  }
  return latest;
}

async function openDiagnostics(): Promise<void> {
  await browser
    .$("//li[contains(@class,'ant-menu-item') and .//*[normalize-space()='设置与诊断']]")
    .click();
  await expect(await browser.$("h2")).toHaveText("设置与诊断");
}

async function refreshUntil(...expected: string[]): Promise<void> {
  await browser.waitUntil(
    async () => {
      await browser.$("button=刷新状态").click();
      const text = await browser.$("body").getText();
      return expected.every((value) => text.includes(value));
    },
    {
      timeout: 90_000,
      interval: 1_000,
      timeoutMsg: `Executor status did not converge: ${expected.join(", ")}`,
    },
  );
}

describe("E4-14 hidden App Executor lifecycle acceptance", () => {
  it("starts, observes, recovers, times out a hang, stops, and leaves cleanup to App exit", async () => {
    await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");
    const preparation = (await browser.tauri.execute(({ core }) =>
      core.invoke("prepare_executor_lifecycle_for_acceptance"),
    )) as ExecutorLifecyclePreparation;
    assert.match(preparation.installationId, UUID_V4);

    await openDiagnostics();
    await waitForText("本地执行器已停止", "暂无本地执行器诊断记录");
    await browser.$("button=启动执行器").click();
    const body = await browser.$("body");
    await browser.waitUntil(
      async () => (await body.getText()).includes("本地执行器运行中"),
      { timeout: 45_000, interval: 500 },
    );
    await waitForText("0.1.0", "e4-14-hidden-app");

    await browser.tauri.execute(({ core }) =>
      core.invoke("inject_executor_crash_for_acceptance"),
    );
    await refreshUntil("本地执行器运行中", "自动恢复次数", "1");

    await browser.tauri.execute(({ core }) =>
      core.invoke("inject_executor_hang_for_acceptance"),
    );
    await browser.$("button=本地紧急停止").click();
    await browser.$("button=确认停止").click();
    await waitForText("暂时无法读取本地执行器状态。请稍后重试。");
    await refreshUntil("本地执行器已停止");

    await browser.$("button=启动执行器").click();
    await waitForText("本地执行器运行中", "e4-14-hidden-app");
    const bodyText = await browser.$("body").getText();
    assert.doesNotMatch(bodyText, /session|token|password|私钥|本机路径/i);

    await browser.tauri.execute(({ core }) => core.invoke("exit_app_for_acceptance"));
    await browser.pause(12_000);
  });
});
