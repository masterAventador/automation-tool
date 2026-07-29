import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";
import {
  openAutomationRuns,
  openTaskCreate,
  waitForStartup,
} from "./navigation";

interface InstallationPreparation {
  readonly installationId: string;
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const PRIVATE_VALUE_PATTERN =
  /\/Applications|Contents\/MacOS|Program Files|chrome\.exe|msedge\.exe|[A-Z]:\\|authorization|token|secret|credential/i;

describe("H8-16F hidden App original-caller MVP journey", () => {
  it("creates, logs in, discovers, excludes, confirms, acts, and renders results", async () => {
    // The journey used to start on the blocked diagnostics page and repair the
    // operating browser from settings. EB-10 (`f34e503`) deleted that user
    // action: the App no longer discovers, offers or falls back to a system
    // browser, so the only browser it can run is the verified embedded
    // Chromium that has to be present before startup. There is nothing left to
    // repair, and the journey now begins where a real operator begins.
    await waitForStartup();
    const preparation = (await browser.tauri.execute(({ core }) =>
      core.invoke("prepare_task_create_form_for_acceptance"),
    )) as InstallationPreparation;
    assert.match(preparation.installationId, UUID_V4);

    await openTaskCreate();
    await expect(await browser.$("h2")).toHaveText("新建运营任务");
    await browser.$("#searchKeyword").setValue("新能源汽车");
    await browser.$("#targetLimit").setValue("2");
    await browser.$("#minimumIntervalSeconds").setValue("1");
    await browser.$("#maximumIntervalSeconds").setValue("1");
    await browser.$("button=创建任务").click();
    const body = await browser.$("body");
    await browser.waitUntil(
      async () => (await body.getText()).includes("任务已创建："),
      { timeout: 60_000, timeoutMsg: "MVP journey did not create its Task" },
    );
    const createdText = await body.getText();
    const taskId = createdText.match(/任务已创建：([0-9a-f-]{36})/)?.[1];
    assert.match(taskId ?? "", UUID_V4);
    await browser.$("button=查看运行详情").click();
    await expect(await browser.$("h3=任务运行详情")).toExist();

    await browser.$("button=开始目标发现").click();
    try {
      await browser.waitUntil(
        async () => {
          const text = await body.getText();
          return text.includes("平台状态") && text.includes("登录正常");
        },
        {
          timeout: 180_000,
          timeoutMsg: "waiting-platform-login did not automatically open the handling path",
        },
      );
    } catch {
      const text = await body.getText();
      const executorStatus = await browser.tauri.execute(({ core }) =>
        core.invoke("get_executor_status"),
      );
      const executorDiagnostics = await browser.tauri.execute(({ core }) =>
        core.invoke("get_executor_diagnostics"),
      );
      const openLoginButton = await browser.$("button=打开登录处理");
      const openLoginButtonExists = await openLoginButton.isExisting();
      throw new Error(
        `waiting-platform-login handoff facts: ${JSON.stringify({
          discoverySubmitted: text.includes("目标发现命令已提交"),
          discovering: text.includes("发现目标中"),
          awaitingLogin: text.includes("等待平台登录"),
          platformPage: text.includes("平台状态"),
          loginHealthy: text.includes("登录正常"),
          platformFailure: text.includes("暂时无法读取抖音登录状态"),
          loginOpenPending: openLoginButtonExists && !(await openLoginButton.isEnabled()),
          loginActionRendered: text.includes("请在打开的运营浏览器中扫码登录"),
          executorStatus,
          executorDiagnostics,
        })}`,
      );
    }
    await expect(await browser.$("h2")).toHaveText("账号与平台");

    await openAutomationRuns();
    await expect(await browser.$("h3=任务运行详情")).toExist();
    await browser.waitUntil(
      async () => (await body.getText()).includes("草稿"),
      {
        timeout: 60_000,
        timeoutMsg: "the preflight-rejected Task did not remain safely restartable",
      },
    );
    await browser.$("button=开始目标发现").click();
    const discoveryFailureFacts = [
      "当前平台登录或任务状态尚未满足目标发现条件，请先处理平台状态后重试",
      "当前设备已有任务正在运行，请先完成或终止该任务后再试",
      "目标发现结果暂时无法确认，请查看权威状态后重试",
    ];
    await browser.waitUntil(
      async () => {
        const text = await body.getText();
        return (
          text.includes("已发现 2 个目标") ||
          discoveryFailureFacts.some((fact) => text.includes(fact))
        );
      },
      { timeout: 120_000, timeoutMsg: "controlled discovery did not render its preview" },
    );
    const discoveryText = await body.getText();
    const discoveryFailure = discoveryFailureFacts.find((fact) =>
      discoveryText.includes(fact),
    );
    assert.equal(
      discoveryFailure,
      undefined,
      discoveryFailure ?? "controlled discovery returned an unknown public failure",
    );
    assert.match(discoveryText, /已发现 2 个目标/);

    const secondTarget = await browser.$("input[aria-label='选择目标 验收目标 2']");
    assert.equal(await secondTarget.isSelected(), true);
    await secondTarget.click();
    await browser.waitUntil(
      async () => (await body.getText()).includes("本次排除 1 个"),
      { timeout: 60_000, timeoutMsg: "target exclusion did not converge" },
    );
    await browser.$("button=确认执行").click();
    const confirmation = await browser.$(".ant-popconfirm-description");
    await expect(confirmation).toExist();
    assert.match(await confirmation.getText(), /动作\s*只浏览/);
    assert.match(await confirmation.getText(), /执行\s*1\s*个目标/);
    await browser.$("button=确认目标").click();

    await browser.waitUntil(
      async () => (await body.getText()).includes("任务已进入终态"),
      { timeout: 180_000, timeoutMsg: "controlled Action did not reach a terminal Task" },
    );
    let finalFacts = {
      success: false,
      excluded: false,
      terminal: false,
      resultUnavailable: false,
      timelineUnavailable: false,
      emptyResults: false,
      firstTarget: false,
      secondTarget: false,
    };
    try {
      await browser.waitUntil(
        async () => {
          const text = await body.getText();
          finalFacts = {
            success: text.includes("目标主页已确认可见"),
            excluded: text.includes("用户在预览中排除此目标"),
            terminal: text.includes("任务已进入终态"),
            resultUnavailable: text.includes("目标结果暂时不可用"),
            timelineUnavailable: text.includes("事件时间线暂时不可用"),
            emptyResults: text.includes("还没有已发现的目标"),
            firstTarget: text.includes("验收目标 1"),
            secondTarget: text.includes("验收目标 2"),
          };
          return finalFacts.success && finalFacts.excluded && finalFacts.terminal;
        },
        { timeout: 20_000 },
      );
    } catch {
      throw new Error(
        `controlled Action result did not reach the App: ${JSON.stringify(finalFacts)}`,
      );
    }
    const finalText = await body.getText();
    assert.match(finalText, /验收目标 1[\s\S]*目标主页已确认可见[\s\S]*成功/);
    assert.match(finalText, /验收目标 2[\s\S]*用户在预览中排除此目标[\s\S]*跳过/);
    assert.doesNotMatch(finalText, PRIVATE_VALUE_PATTERN);
    assert.doesNotMatch(finalText, /产品登录|注册账号|账号登录/);

    await browser.tauri.execute(({ core }) => core.invoke("exit_app_for_acceptance"));
  });
});
