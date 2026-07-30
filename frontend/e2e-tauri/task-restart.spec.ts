import assert from "node:assert/strict";
import { access, writeFile } from "node:fs/promises";

import { browser } from "@wdio/globals";
import {
  openAutomationRuns,
  openTaskCreate,
  waitForStartup,
} from "./navigation";

interface TaskRestartPreparation {
  readonly installationId: string;
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function requiredSignalPath(name: string): string {
  const value = process.env[name];
  if (value === undefined || value.length === 0) {
    throw new Error(`Missing T3-20 signal path: ${name}`);
  }
  return value;
}

async function signal(path: string): Promise<void> {
  await writeFile(path, "ready\n", { encoding: "utf8", flag: "wx" });
}

async function waitForSignal(path: string): Promise<void> {
  await browser.waitUntil(
    async () => {
      try {
        await access(path);
        return true;
      } catch {
        return false;
      }
    },
    { timeout: 90_000, timeoutMsg: "T3-20 orchestrator signal was not observed" },
  );
}

async function waitForRenderedText(...expected: string[]): Promise<string> {
  const body = await browser.$("body");
  let latestText = "";
  try {
    await browser.waitUntil(
      async () => {
        latestText = await body.getText();
        return expected.every((value) => latestText.includes(value));
      },
      {
        timeout: 90_000,
        timeoutMsg: `Restart page did not render: ${expected.join(", ")}`,
      },
    );
  } catch {
    throw new Error(`Latest restart text: ${latestText}`);
  }
  return latestText;
}

describe("T3-20 hidden App restart recovery acceptance", () => {
  it("survives a real Control Plane restart and restores PostgreSQL facts", async () => {
    const readyPath = requiredSignalPath("AUTOMATION_TOOL_T320_READY_FILE");
    const downPath = requiredSignalPath("AUTOMATION_TOOL_T320_DOWN_FILE");
    const unavailablePath = requiredSignalPath("AUTOMATION_TOOL_T320_UNAVAILABLE_FILE");
    const upPath = requiredSignalPath("AUTOMATION_TOOL_T320_UP_FILE");

    await waitForStartup();
    const preparation = (await browser.tauri.execute(({ core }) =>
      core.invoke("prepare_task_restart_for_acceptance"),
    )) as TaskRestartPreparation;
    assert.match(preparation.installationId, UUID_V4);

    await openTaskCreate();
    await browser.$("#searchKeyword").setValue("T3-20 重启恢复");
    await browser.$("#targetLimit").setValue("3");
    await browser.$("button=创建任务").click();
    const created = await waitForRenderedText("任务已创建：", "查看运行详情");
    const taskId = created.match(/任务已创建：([0-9a-f-]{36})/)?.[1];
    assert.match(taskId ?? "", UUID_V4);
    await browser.$("button=查看运行详情").click();
    await waitForRenderedText("运行中", "任务开始", "步骤开始");

    await browser.$("button=取消任务").click();
    await browser.$("button=确认取消").click();
    await waitForRenderedText("取消命令已提交", "正在取消");
    await signal(readyPath);

    await waitForSignal(downPath);
    await browser.refresh();
    await waitForRenderedText("控制服务不可用", "重新检查");
    await signal(unavailablePath);

    await waitForSignal(upPath);
    await browser.$("button=重新检查").click();
    await openAutomationRuns();
    // 列表的行名改版后是创建时刻，不再印 UUID；标识作为惰性的 `data-task-id` 还在。
    // 详情页仍印完整 UUID，所以下一句按文本等的断言不动。
    await browser.waitUntil(
      async () => browser.$(`button[data-task-id="${taskId}"]`).isExisting(),
      { timeout: 90_000, timeoutMsg: `运行记录里没有出现任务 ${taskId}` },
    );
    await waitForRenderedText("已取消");
    await browser.$(`button[data-task-id="${taskId}"]`).click();
    await waitForRenderedText("任务运行详情", taskId ?? "", "已取消", "任务已取消");
    assert.equal(/产品登录|注册账号|账号登录/.test(await browser.$("body").getText()), false);
  });
});
