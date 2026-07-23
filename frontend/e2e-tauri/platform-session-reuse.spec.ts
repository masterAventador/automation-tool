import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

type AcceptancePhase = "first" | "restart" | "expired" | "risk";

interface Preparation {
  readonly installationId: string;
}

interface PlatformSnapshot {
  readonly platform: string;
  readonly state: string;
  readonly observedAt: string | null;
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const PHASES = new Set<AcceptancePhase>(["first", "restart", "expired", "risk"]);
const ACTION_FACTS = {
  healthy: "登录正常",
  expired: "请在打开的运营浏览器中扫码登录。",
  risk: "页面需要人工处理，请在运营浏览器中完成后重新检查。",
} as const;

function acceptancePhase(): AcceptancePhase {
  const value = process.env.AUTOMATION_TOOL_B515_PHASE;
  assert.ok(PHASES.has(value as AcceptancePhase), "B5-15 phase is unavailable");
  return value as AcceptancePhase;
}

async function openPlatformPage(expectedSnapshot: string): Promise<void> {
  await browser.$("li=平台状态").click();
  await expect(await browser.$("h2")).toHaveText("平台状态");
  await browser.waitUntil(
    async () => (await browser.$("body").getText()).includes(expectedSnapshot),
    { timeout: 60_000, timeoutMsg: "authoritative platform snapshot did not converge" },
  );
}

async function runRecheck(expectedAction: string): Promise<string> {
  const recheck = await browser.$("button=我已处理，重新检查");
  await recheck.click();
  await browser.waitUntil(
    async () => (await browser.$("body").getText()).includes(expectedAction),
    { timeout: 120_000, timeoutMsg: "B5-15 page fact did not converge" },
  );
  await browser.waitUntil(() => recheck.isEnabled(), {
    timeout: 120_000,
    timeoutMsg: "B5-15 recheck did not settle",
  });
  return browser.$("body").getText();
}

async function waitForAuthoritativeState(expectedState: string): Promise<void> {
  await browser.waitUntil(
    async () => {
      const snapshot = (await browser.tauri.execute(({ core }) =>
        core.invoke("get_douyin_platform_session"),
      )) as PlatformSnapshot;
      return snapshot.platform === "douyin" && snapshot.state === expectedState;
    },
    { timeout: 60_000, timeoutMsg: "authoritative Session state did not converge" },
  );
}

describe("B5-15 platform Session restart acceptance", () => {
  it("reuses one Profile across App/Executor/browser restarts and enters handoff", async () => {
    const phase = acceptancePhase();
    await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");

    if (phase === "first") {
      const preparation = (await browser.tauri.execute(({ core }) =>
        core.invoke("prepare_platform_session_reuse_for_acceptance"),
      )) as Preparation;
      assert.match(preparation.installationId, UUID_V4);
      await openPlatformPage("尚未确认");
      const open = await browser.$("button=打开登录处理");
      await open.click();
      await browser.waitUntil(
        async () => (await browser.$("body").getText()).includes(ACTION_FACTS.healthy),
        { timeout: 120_000, timeoutMsg: "first persistent Profile check was not healthy" },
      );
      await browser.waitUntil(() => open.isEnabled(), { timeout: 120_000 });
      const body = await browser.$("body").getText();
      assert.ok(!body.includes(ACTION_FACTS.expired));
      assert.ok(!body.includes(ACTION_FACTS.risk));
      await waitForAuthoritativeState("healthy");
    } else if (phase === "restart") {
      await openPlatformPage("登录正常");
      const body = await runRecheck(ACTION_FACTS.healthy);
      assert.ok(!body.includes(ACTION_FACTS.expired));
      assert.ok(!body.includes(ACTION_FACTS.risk));
      await waitForAuthoritativeState("healthy");
    } else if (phase === "expired") {
      await openPlatformPage("登录正常");
      const body = await runRecheck(ACTION_FACTS.expired);
      assert.ok(!body.includes(ACTION_FACTS.risk));
      await waitForAuthoritativeState("expired");
    } else {
      await openPlatformPage("登录已过期");
      const body = await runRecheck(ACTION_FACTS.risk);
      assert.ok(body.includes("需要人工处理"));
      await waitForAuthoritativeState("risk");
    }

    await browser.tauri.execute(({ core }) => core.invoke("exit_app_for_acceptance"));
  });
});
