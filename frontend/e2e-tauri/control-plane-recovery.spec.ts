import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { writeFile } from "node:fs/promises";

import { browser } from "@wdio/globals";
import {
  openAutomationRuns,
  waitForTaskRow,
  openTaskCreate,
  waitForStartup,
} from "./navigation";

interface Preparation {
  readonly installationId: string;
}

interface ExecutorStatus {
  readonly restartCount: number;
  readonly state: string;
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (value === undefined || value.length === 0) throw new Error(`Missing H8-06 value: ${name}`);
  return value;
}

async function signal(path: string, value: unknown = {}): Promise<void> {
  await writeFile(path, `${JSON.stringify(value)}\n`, {
    encoding: "utf8",
    mode: 0o600,
    flag: "wx",
  });
}

async function waitForSignal(path: string, label: string): Promise<void> {
  await browser.waitUntil(() => existsSync(path), {
    timeout: 120_000,
    interval: 100,
    timeoutMsg: `H8-06 runner did not publish ${label}`,
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
        timeoutMsg: `H8-06 page did not render: ${expected.join(", ")}`,
      },
    );
  } catch {
    throw new Error(`Latest H8-06 text: ${latest}`);
  }
  return latest;
}

describe("H8-06 hidden App Control Plane restart recovery acceptance", () => {
  it("keeps one Executor process and converges the App-submitted command exactly once", async () => {
    await waitForStartup();
    const preparation = (await browser.tauri.execute(({ core }) =>
      core.invoke("prepare_control_plane_recovery_for_acceptance"),
    )) as Preparation;
    assert.match(preparation.installationId, UUID_V4);

    await openTaskCreate();
    await browser.$("#searchKeyword").setValue("H8-06 Control Plane 重启恢复");
    const actionInput = await browser.$("#action");
    await browser.execute(() => {
      const input = document.querySelector<HTMLInputElement>("#action");
      const select = input?.closest<HTMLElement>(".ant-select");
      if (select === undefined || select === null) throw new Error("H8-06 action Select missing");
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
    const appProcessId = (await browser.tauri.execute(({ core }) =>
      core.invoke("app_process_id_for_acceptance"),
    )) as number;
    assert.equal(Number.isSafeInteger(appProcessId) && appProcessId > 1, true);
    await signal(requiredEnvironment("AUTOMATION_TOOL_H806_TASK_CREATED_SIGNAL"), {
      appProcessId,
      installationId: preparation.installationId,
      taskId,
    });

    await waitForSignal(
      requiredEnvironment("AUTOMATION_TOOL_H806_TASK_SEEDED_SIGNAL"),
      "the running Task fixture",
    );
    const started = (await browser.tauri.execute(({ core }) =>
      core.invoke("restart_executor"),
    )) as ExecutorStatus;
    assert.equal(started.state, "running");
    assert.equal(started.restartCount, 0);

    await openAutomationRuns();

    // 列表行名改版后是创建时刻、不印 UUID；标识在 data-task-id 上。
    await waitForTaskRow(taskId ?? "");
    await waitForText("运行中", "本机执行器在线");
    await browser.$(`button[data-task-id="${taskId ?? ""}"]`).click();
    await waitForText("任务运行详情", taskId ?? "", "运行中", "任务开始", "步骤开始");
    await signal(requiredEnvironment("AUTOMATION_TOOL_H806_EXECUTOR_READY_SIGNAL"));

    await waitForSignal(
      requiredEnvironment("AUTOMATION_TOOL_H806_EXECUTOR_SUSPENDED_SIGNAL"),
      "the suspended Executor",
    );
    await browser.$("button=取消任务").click();
    await browser.$("button=确认取消").click();
    await waitForText("取消命令已提交", "正在取消");
    await signal(requiredEnvironment("AUTOMATION_TOOL_H806_CANCEL_SUBMITTED_SIGNAL"));

    await waitForSignal(
      requiredEnvironment("AUTOMATION_TOOL_H806_CONTROL_PLANE_DOWN_SIGNAL"),
      "the stopped Control Plane",
    );
    await browser.refresh();
    await waitForText("控制服务不可用", "重新检查");
    const duringRestart = (await browser.tauri.execute(({ core }) =>
      core.invoke("get_executor_status"),
    )) as ExecutorStatus;
    assert.equal(duringRestart.state, "running");
    assert.equal(duringRestart.restartCount, 0);
    await signal(requiredEnvironment("AUTOMATION_TOOL_H806_UNAVAILABLE_SIGNAL"));

    await waitForSignal(
      requiredEnvironment("AUTOMATION_TOOL_H806_CONTROL_PLANE_UP_SIGNAL"),
      "the restarted Control Plane",
    );
    await browser.$("button=重新检查").click();
    // 过了控制服务闸之后落回默认页（AI 助理），先导航回运行记录。
    await openAutomationRuns();
    await waitForTaskRow(taskId ?? "");
    await waitForText("已取消", "本机执行器在线");
    await browser.$(`button[data-task-id="${taskId ?? ""}"]`).click();
    await waitForText("任务运行详情", taskId ?? "", "已取消", "任务已取消");
    const recovered = (await browser.tauri.execute(({ core }) =>
      core.invoke("get_executor_status"),
    )) as ExecutorStatus;
    assert.equal(recovered.state, "running");
    assert.equal(recovered.restartCount, 0);
    await signal(requiredEnvironment("AUTOMATION_TOOL_H806_RECOVERED_SIGNAL"), {
      installationId: preparation.installationId,
      restart_count: recovered.restartCount,
      taskId,
    });
  });
});
