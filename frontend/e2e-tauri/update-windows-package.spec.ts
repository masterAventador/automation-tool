import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { setTimeout as delay } from "node:timers/promises";

import { browser } from "@wdio/globals";
import {
  openSettings,
  waitForStartup,
} from "./navigation";

interface UpdateState {
  readonly state: string;
  readonly action?: string;
  readonly code?: string;
  readonly stage?: string;
}

async function readState(): Promise<UpdateState> {
  return (await browser.tauri.execute(({ core }) =>
    core.invoke("get_app_update_state"),
  )) as UpdateState;
}

async function waitForState(state: string, action?: string): Promise<UpdateState> {
  try {
    await browser.waitUntil(
      async () => {
        const current = await readState();
        return current.state === state && (action === undefined || current.action === action);
      },
      { interval: 100, timeout: 30_000, timeoutMsg: `update did not reach ${state}/${action}` },
    );
  } catch {
    throw new Error(
      `update did not reach ${state}/${action}; state=${JSON.stringify(await readState())}`,
    );
  }
  return readState();
}

async function waitForText(text: string): Promise<void> {
  const body = await browser.$("body");
  try {
    await browser.waitUntil(async () => (await body.getText()).includes(text), {
      interval: 100,
      timeout: 30_000,
      timeoutMsg: `packaged update UI did not render ${text}`,
    });
  } catch {
    throw new Error(
      `packaged update UI did not render ${text}; state=${JSON.stringify(await readState())}`,
    );
  }
}

async function clickEnabledButton(label: string): Promise<void> {
  const button = await browser.$(`button=${label}`);
  await button.waitForEnabled({ timeout: 30_000 });
  await button.click();
}

async function waitForInstalledBinary(): Promise<void> {
  const binary = process.env.H822_WINDOWS_APP_BINARY;
  const expected = process.env.H822_WINDOWS_EXPECTED_BINARY_SHA256;
  if (binary === undefined || expected === undefined) {
    throw new Error("Windows package update hash boundary is unavailable");
  }
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    try {
      const actual = createHash("sha256").update(await readFile(binary)).digest("hex");
      if (actual === expected) return;
    } catch {
      // NSIS removes the old executable before placing the new version.
    }
    await delay(100);
  }
  throw new Error("official Windows updater did not replace the installed App binary");
}

describe("H8-22 unsigned Windows NSIS package update acceptance", () => {
  it("drives the requested package scenario through the original App", async () => {
    await waitForStartup();
    const scenario = process.env.H822_WINDOWS_SCENARIO;

    if (scenario === "optional-decisions") {
      await waitForText("发现新版本 0.2.0");
      await clickEnabledButton("稍后提醒");
      await waitForState("ready", "deferred");
      await openSettings();
      await browser.$("button=检查更新").click();
      await waitForText("发现新版本 0.2.0");
      await clickEnabledButton("跳过此版本");
      await waitForState("ready", "skipped");
      await browser.$("button=检查更新").click();
      await waitForState("ready", "suppressed");
      await browser.$("button=检查更新").click();
      await waitForText("发现新版本 0.3.0");
      return;
    }

    if (scenario === "optional-install") {
      await waitForText("发现新版本 0.3.0");
      try {
        await clickEnabledButton("立即安装");
      } catch (error) {
        if (!(error instanceof Error) || !error.message.includes("ECONNREFUSED")) {
          throw error;
        }
      }
      await waitForInstalledBinary();
      return;
    }

    if (scenario === "forced-first") {
      await waitForText("必须更新到 0.2.0");
      await waitForState("ready", "forced");
      assert.equal(await browser.$("button=稍后提醒").isExisting(), false);
      assert.equal(await browser.$("button=跳过此版本").isExisting(), false);
      await browser.keys("Escape");
      await waitForText("必须更新到 0.2.0");
      return;
    }

    if (scenario === "installer-failure") {
      await waitForText("发现新版本 0.4.0");
      await clickEnabledButton("立即安装");
      const failed = await waitForState("failed");
      assert.equal(failed.stage, "install");
      assert.equal(failed.code, "installation_failed");
      await openSettings();
      await waitForText("更新当前不可用");
      return;
    }

    if (scenario === "verify-installed") {
      await waitForState("up_to_date");
      return;
    }

    throw new Error("H822_WINDOWS_SCENARIO is invalid");
  });
});
