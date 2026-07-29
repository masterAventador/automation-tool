import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";
import {
  openWorkbenchSection,
  waitForStartup,
} from "./navigation";

interface Preparation {
  readonly installationId: string;
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

const ACTION_FACTS = [
  "请在打开的运营浏览器中扫码登录。",
  "扫码成功，请在手机抖音中确认登录。",
  "二维码已过期，请重新打开登录处理。",
  "页面需要人工处理，请在运营浏览器中完成后重新检查。",
  "抖音仍未登录，请在运营浏览器中继续处理。",
  "暂时无法确认页面状态，请检查运营浏览器后重试。",
  "登录正常",
];

async function waitForOneFact(): Promise<void> {
  await browser.waitUntil(
    async () => {
      const text = await browser.$("body").getText();
      return ACTION_FACTS.some((fact) => text.includes(fact));
    },
    { timeout: 120_000, timeoutMsg: "platform handling produced no public page fact" },
  );
}

describe("B5-13/B5-14 platform Session production-path acceptance", () => {
  it("queries status, drives the headless Executor, and safely logs out from the hidden App UI", async () => {
    await waitForStartup();
    const preparation = (await browser.tauri.execute(({ core }) =>
      core.invoke("prepare_platform_session_for_acceptance"),
    )) as Preparation;
    assert.match(preparation.installationId, UUID_V4);

    await openWorkbenchSection("账号与平台");
    await expect(await browser.$("h2")).toHaveText("账号与平台");
    await browser.waitUntil(
      async () => (await browser.$("body").getText()).includes("尚未确认"),
      { timeout: 60_000, timeoutMsg: "real Control Plane Session query did not render" },
    );

    const open = await browser.$("button=打开登录处理");
    await open.click();
    await waitForOneFact();
    await browser.waitUntil(() => open.isEnabled(), {
      timeout: 120_000,
      timeoutMsg: "open handling did not settle",
    });

    const recheck = await browser.$("button=我已处理，重新检查");
    await recheck.click();
    await waitForOneFact();
    await browser.waitUntil(() => recheck.isEnabled(), {
      timeout: 120_000,
      timeoutMsg: "platform recheck did not settle",
    });
    const logout = await browser.$("button=安全注销");
    assert.equal(await logout.isEnabled(), true);
    await logout.click();
    await browser.$("button=确认注销").click();
    try {
      await browser.waitUntil(
        async () => {
          const text = await browser.$("body").getText();
          return text.includes("需要登录") && (await logout.isEnabled());
        },
        { timeout: 180_000 },
      );
    } catch {
      // A bare timeout here costs another three-minute run to learn anything,
      // because the page swallows a failed logout into one generic sentence.
      // Report what the page and the authoritative projection actually said,
      // the way the other specs in this directory already do.
      const text = await browser.$("body").getText();
      const session = await browser.tauri.execute(({ core }) =>
        core.invoke("get_douyin_platform_session"),
      );
      throw new Error(
        `safe logout did not render authoritative missing state: ${JSON.stringify({
          authoritativeSession: session,
          logoutStillPending: !(await logout.isEnabled()),
          rendersMissing: text.includes("需要登录"),
          rendersHealthy: text.includes("登录正常"),
          rendersUnknown: text.includes("尚未确认"),
          rendersReadFailure: text.includes("暂时无法读取抖音登录状态"),
        })}`,
      );
    }

    const blocked = (await browser.tauri.execute(async ({ core }) => {
      try {
        await core.invoke("create_douyin_search_exposure_task", {
          definition: {
            template: "douyin.search_exposure.v1",
            searchKeyword: "新能源汽车",
            action: "browse",
            messageTemplate: null,
            targetLimit: 10,
            minimumIntervalSeconds: 30,
            maximumIntervalSeconds: 90,
            previewRequired: true,
            finalConfirmationRequired: true,
          },
          idempotencyKey: "task:b514:blocked-after-logout",
        });
        return false;
      } catch {
        return true;
      }
    })) as boolean;
    assert.equal(blocked, true, "real App Task API must observe the persistent logout gate");

    await browser.tauri.execute(({ core }) => core.invoke("exit_app_for_acceptance"));
  });
});
