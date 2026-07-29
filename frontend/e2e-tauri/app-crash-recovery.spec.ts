import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { writeFile } from "node:fs/promises";

import { browser } from "@wdio/globals";
import {
  openAutomationRuns,
  openTaskCreate,
  waitForStartup,
} from "./navigation";

interface AppCrashRecoveryPreparation {
  readonly installationId: string;
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const phase = process.env.AUTOMATION_TOOL_H804_PHASE;

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (value === undefined || value.length === 0) {
    throw new Error(`Missing H8-04 environment value: ${name}`);
  }
  return value;
}

async function signal(path: string, value: unknown): Promise<void> {
  await writeFile(path, `${JSON.stringify(value)}\n`, {
    encoding: "utf8",
    mode: 0o600,
    flag: "wx",
  });
}

async function waitForSignal(path: string, label: string): Promise<void> {
  await browser.waitUntil(() => existsSync(path), {
    timeout: 120_000,
    timeoutMsg: `H8-04 runner did not publish ${label}`,
  });
}

async function waitForRenderedText(...expected: string[]): Promise<string> {
  const body = await browser.$("body");
  let latest = "";
  try {
    await browser.waitUntil(
      async () => {
        latest = await body.getText();
        return expected.every((value) => latest.includes(value));
      },
      {
        timeout: 120_000,
        timeoutMsg: `H8-04 page did not render: ${expected.join(", ")}`,
      },
    );
  } catch {
    throw new Error(`Latest H8-04 text: ${latest}`);
  }
  return latest;
}

describe("H8-04 hidden App crash recovery acceptance", () => {
  it("restores the authoritative running Task without replaying work", async () => {
    await waitForStartup();

    if (phase === "before-crash") {
      const preparation = (await browser.tauri.execute(({ core }) =>
        core.invoke("prepare_app_crash_recovery_for_acceptance"),
      )) as AppCrashRecoveryPreparation;
      assert.match(preparation.installationId, UUID_V4);

      await openTaskCreate();
      await browser.$("#searchKeyword").setValue("H8-04 App 崩溃恢复");
      const actionInput = await browser.$("#action");
      await browser.execute(() => {
        const input = document.querySelector<HTMLInputElement>("#action");
        const select = input?.closest<HTMLElement>(".ant-select");
        if (select === undefined || select === null) {
          throw new Error("H8-04 Task action Select root is missing");
        }
        select.dispatchEvent(
          new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window }),
        );
      });
      await browser.waitUntil(
        async () => (await actionInput.getAttribute("aria-expanded")) === "true",
        { timeout: 5_000, timeoutMsg: "H8-04 Task action Select did not open" },
      );
      await browser.$(".ant-select-item-option[title='评论']").click();
      await browser
        .$("#messageTemplate")
        .setValue("您好 {{target_display_name}} 期待您的分享");
      await browser.$("#targetLimit").setValue("3");
      await browser.$("button=创建任务").click();
      const created = await waitForRenderedText("任务已创建：", "查看运行详情");
      const taskId = created.match(/任务已创建：([0-9a-f-]{36})/)?.[1];
      assert.match(taskId ?? "", UUID_V4);
      await signal(requiredEnvironment("AUTOMATION_TOOL_H804_TASK_CREATED_SIGNAL"), {
        installationId: preparation.installationId,
        taskId,
      });

      await waitForSignal(
        requiredEnvironment("AUTOMATION_TOOL_H804_TASK_SEEDED_SIGNAL"),
        "the running Task fixture",
      );
      const executor = (await browser.tauri.execute(({ core }) =>
        core.invoke("restart_executor"),
      )) as { readonly state: string };
      assert.equal(executor.state, "running");
        await openAutomationRuns();
        await waitForRenderedText(
        taskId ?? "",
        "本机执行器在线",
        "运行中",
      );
      await browser.$(`button=${taskId ?? ""}`).click();
      await waitForRenderedText(
        "任务运行详情",
        taskId ?? "",
        "运行中",
        "任务开始",
        "步骤开始",
      );
      const appProcessId = (await browser.tauri.execute(({ core }) =>
        core.invoke("app_process_id_for_acceptance"),
      )) as number;
      assert.ok(Number.isSafeInteger(appProcessId) && appProcessId > 1);
      await signal(requiredEnvironment("AUTOMATION_TOOL_H804_CRASH_READY_SIGNAL"), {
        appProcessId,
        installationId: preparation.installationId,
        taskId,
      });

      await browser.waitUntil(() => false, {
        timeout: 240_000,
        timeoutMsg: "H8-04 runner did not kill the first App",
      });
      return;
    }

    if (phase !== "after-crash") {
      throw new Error(`Unsupported H8-04 phase: ${phase ?? "missing"}`);
    }
    const taskId = requiredEnvironment("AUTOMATION_TOOL_H804_TASK_ID");
    assert.match(taskId, UUID_V4);
    const retry = await browser.$("button=重新加载工作台");
    await browser.waitUntil(
      async () => {
        const body = await browser.$("body").getText();
        return (
          (await retry.isExisting()) ||
          (body.includes(taskId) && body.includes("本机执行器在线") && body.includes("运行中"))
        );
      },
      { timeout: 120_000, timeoutMsg: "H8-04 workbench did not restore its snapshot" },
    );
    if (await retry.isExisting()) await retry.click();
    await waitForRenderedText(taskId, "本机执行器在线", "运行中");
    await browser.$(`button=${taskId}`).click();
    await waitForRenderedText(
      "任务运行详情",
      taskId,
      "运行中",
      "任务开始",
      "步骤开始",
    );
    assert.equal(/产品登录|注册账号|账号登录/.test(await browser.$("body").getText()), false);
    const appProcessId = (await browser.tauri.execute(({ core }) =>
      core.invoke("app_process_id_for_acceptance"),
    )) as number;
    assert.ok(Number.isSafeInteger(appProcessId) && appProcessId > 1);
    await signal(requiredEnvironment("AUTOMATION_TOOL_H804_RECOVERED_SIGNAL"), {
      appProcessId,
      taskId,
    });
  });
});
