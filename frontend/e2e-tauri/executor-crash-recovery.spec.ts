import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { writeFile } from "node:fs/promises";

import { browser } from "@wdio/globals";
import {
  openTaskCreate,
  openWorkbenchSection,
  waitForStartup,
} from "./navigation";

interface Preparation {
  readonly installationId: string;
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (value === undefined || value.length === 0) throw new Error(`Missing H8-05 value: ${name}`);
  return value;
}

async function signal(path: string, value: unknown): Promise<void> {
  await writeFile(path, `${JSON.stringify(value)}\n`, {
    encoding: "utf8",
    mode: 0o600,
    flag: "wx",
  });
}

async function waitForSignal(path: string): Promise<void> {
  await browser.waitUntil(() => existsSync(path), {
    timeout: 120_000,
    timeoutMsg: "H8-05 runner did not publish the seeded ledger",
  });
}

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
        timeout: 120_000,
        interval: 500,
        timeoutMsg: `H8-05 page did not render: ${expected.join(", ")}`,
      },
    );
  } catch {
    throw new Error(`Latest H8-05 text: ${latest}`);
  }
  return latest;
}

describe("H8-05 hidden App Executor crash recovery acceptance", () => {
  it("restarts once, aligns the ledger, and never redispatches the uncertain effect", async () => {
    await waitForStartup();
    const preparation = (await browser.tauri.execute(({ core }) =>
      core.invoke("prepare_executor_crash_recovery_for_acceptance"),
    )) as Preparation;
    assert.match(preparation.installationId, UUID_V4);

    await openTaskCreate();
    await browser.$("#searchKeyword").setValue("H8-05 Executor 崩溃恢复");
    const actionInput = await browser.$("#action");
    await browser.execute(() => {
      const input = document.querySelector<HTMLInputElement>("#action");
      const select = input?.closest<HTMLElement>(".ant-select");
      if (select === undefined || select === null) throw new Error("H8-05 action Select missing");
      select.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
    });
    await browser.waitUntil(async () => (await actionInput.getAttribute("aria-expanded")) === "true");
    await browser.$(".ant-select-item-option[title='评论']").click();
    await browser.$("#messageTemplate").setValue("您好 {{target_display_name}} 期待您的分享");
    await browser.$("#targetLimit").setValue("3");
    await browser.$("button=创建任务").click();
    const created = await waitForText("任务已创建：", "查看运行详情");
    const taskId = created.match(/任务已创建：([0-9a-f-]{36})/)?.[1];
    assert.match(taskId ?? "", UUID_V4);
    await signal(requiredEnvironment("AUTOMATION_TOOL_H805_TASK_CREATED_SIGNAL"), {
      installationId: preparation.installationId,
      taskId,
    });

    await waitForSignal(requiredEnvironment("AUTOMATION_TOOL_H805_TASK_SEEDED_SIGNAL"));
    const started = (await browser.tauri.execute(({ core }) =>
      core.invoke("restart_executor"),
    )) as { readonly restartCount: number; readonly state: string };
    assert.equal(started.state, "running");
    assert.equal(started.restartCount, 0);

    await openWorkbenchSection("设置");
    await waitForText("本地执行器运行中");
    await browser.tauri.execute(({ core }) => core.invoke("inject_executor_crash_for_acceptance"));
    await browser.waitUntil(
      async () => {
        await browser.$("button=刷新状态").click();
        const text = await browser.$("body").getText();
        return text.includes("本地执行器运行中") && text.includes("自动恢复次数") && text.includes("1");
      },
      { timeout: 120_000, interval: 1_000, timeoutMsg: "H8-05 supervisor did not recover once" },
    );

    await waitForText(taskId ?? "", "结果待确认", "本机执行器在线");
    await browser.$(`button=${taskId ?? ""}`).click();
    await waitForText("任务运行详情", taskId ?? "", "结果待确认", "结果待确认");
    await signal(requiredEnvironment("AUTOMATION_TOOL_H805_RECOVERED_SIGNAL"), {
      installationId: preparation.installationId,
      restart_count: 1,
      taskId,
    });
  });
});
