import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";
import {
  openWorkbenchSection,
  waitForStartup,
} from "./navigation";

interface ExecutorLifecyclePreparation {
  readonly installationId: string;
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const PRIVATE_DIAGNOSTIC_VALUES =
  /private-access-token|sid_tt=private|sessionid=hidden|private-token|hunter2|0001020304050607|private-cookie|hidden-cookie|atds1\.private|atlep1\.private|example\.com|\/Users\/alice|C:\\Users\\alice|file:\/\/\/private|data:image|private_user|db\.example|private author text|<div>private<\/div>|private-node|private reply|download\.example|X-Amz/i;

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
  await openWorkbenchSection("设置");
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
    await waitForStartup();
    const preparation = (await browser.tauri.execute(({ core }) =>
      core.invoke("prepare_executor_lifecycle_for_acceptance"),
    )) as ExecutorLifecyclePreparation;
    assert.match(preparation.installationId, UUID_V4);

    await openDiagnostics();
    await waitForText("本地执行器已停止", "暂无本地执行器诊断记录");
    await browser.tauri.execute(({ core }) =>
      core.invoke("inject_hostile_executor_diagnostics_for_acceptance"),
    );
    await browser.$("button=刷新状态").click();
    await waitForText(
      "request=[REDACTED]",
      "database=[REDACTED]",
      "page_content=[REDACTED] html=[REDACTED] dom=[REDACTED] comment_text=[REDACTED]",
      "signed_url=[REDACTED]",
    );
    const redactedBody = await browser.$("body").getText();
    assert.doesNotMatch(redactedBody, PRIVATE_DIAGNOSTIC_VALUES);
    const successfulDiagnostics = await browser.$(
      "[role='switch'][aria-label='保存成功任务的脱敏诊断']",
    );
    assert.equal(await successfulDiagnostics.getAttribute("aria-checked"), "false");
    await successfulDiagnostics.click();
    await browser.waitUntil(
      async () => (await successfulDiagnostics.getAttribute("aria-checked")) === "true",
      { timeout: 10_000, interval: 200 },
    );
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
    await browser.waitUntil(
      async () => {
        const text = await browser.$("body").getText();
        return (
          text.includes("本地执行器已停止") ||
          text.includes("暂时无法读取本地执行器状态。请稍后重试。")
        );
      },
      { timeout: 90_000, interval: 500 },
    );
    const stoppedOrTimedOut = await browser.$("body").getText();
    if (!stoppedOrTimedOut.includes("本地执行器已停止")) {
      await refreshUntil("本地执行器已停止");
    }

    await browser.$("button=启动执行器").click();
    await waitForText("本地执行器运行中", "e4-14-hidden-app");
    const bodyText = await browser.$("body").getText();
    assert.doesNotMatch(bodyText, PRIVATE_DIAGNOSTIC_VALUES);

    await browser.tauri.execute(({ core }) => core.invoke("exit_app_for_acceptance"));
  });
});
