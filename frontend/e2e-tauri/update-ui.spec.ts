import assert from "node:assert/strict";

import { browser } from "@wdio/globals";
import {
  waitForStartup,
} from "./navigation";

interface UpdateState {
  readonly state: string;
  readonly action?: string;
  readonly release?: { readonly version: string; readonly policy: string };
}

async function readState(): Promise<UpdateState> {
  return (await browser.tauri.execute(({ core }) =>
    core.invoke("get_app_update_state"),
  )) as UpdateState;
}

async function waitForState(state: string, action?: string): Promise<UpdateState> {
  await browser.waitUntil(
    async () => {
      const current = await readState();
      return current.state === state && (action === undefined || current.action === action);
    },
    { interval: 100, timeout: 20_000, timeoutMsg: `update UI did not reach ${state}/${action}` },
  );
  return readState();
}

async function waitForText(text: string): Promise<void> {
  const body = await browser.$("body");
  await browser.waitUntil(async () => (await body.getText()).includes(text), {
    interval: 100,
    timeout: 20_000,
    timeoutMsg: `update UI did not render ${text}`,
  });
}

describe("H8-21/H8-22 hidden original-App update decision and UI acceptance", () => {
  it("drives optional and forced update presentation through visible App controls", async () => {
    await waitForStartup();
    // Every decision below stays inside the one window the user opened: nothing
    // in this flow may hand the install off to a second Tauri window.
    assert.deepEqual(await browser.tauri.listWindows(), ["main"]);
    const scenario = process.env.H821_SCENARIO;

    if (scenario === "optional") {
      await waitForText("发现新版本 0.2.0");
      const offered = await waitForState("ready", "prompt");
      assert.equal(offered.release?.version, "0.2.0");
      assert.equal(offered.release?.policy, "optional");
      await browser.$("button=稍后提醒").click();
      await waitForState("ready", "deferred");

      await browser.$("li=设置与诊断").click();
      await waitForText("已暂缓，将在下次检查时重新提示");
      await browser.$("button=检查更新").click();
      await waitForText("发现新版本 0.2.0");
      await browser.$("button=跳过此版本").click();
      await waitForState("ready", "skipped");

      await browser.$("button=检查更新").click();
      await waitForState("ready", "suppressed");
      await browser.$("button=检查更新").click();
      await waitForText("发现新版本 0.3.0");
      await browser.$("button=立即安装").click();
      const launched = await waitForState("installation_launched");
      assert.equal(launched.release?.version, "0.3.0");
      return;
    }

    if (scenario === "forced-first") {
      await waitForText("必须更新到 0.2.0");
      await waitForText("请重新启动 App，更新将在启动时自动安装。");
      const forced = await waitForState("ready", "forced");
      assert.equal(forced.release?.version, "0.2.0");
      assert.equal(forced.release?.policy, "forced");
      assert.equal(await browser.$("button=稍后提醒").isExisting(), false);
      assert.equal(await browser.$("button=跳过此版本").isExisting(), false);
      await browser.keys("Escape");
      await waitForText("必须更新到 0.2.0");
      return;
    }

    if (scenario === "forced-reopen") {
      const launched = await waitForState("installation_launched");
      assert.equal(launched.release?.version, "0.2.0");
      assert.equal(launched.release?.policy, "forced");
      return;
    }

    throw new Error("H821_SCENARIO is invalid");
  });
});
