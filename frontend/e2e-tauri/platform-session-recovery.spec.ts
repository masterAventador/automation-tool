import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { writeFile } from "node:fs/promises";

import { browser, expect } from "@wdio/globals";
import {
  waitForStartup,
} from "./navigation";

interface Preparation {
  readonly installationId: string;
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

/** The operations browser is only handed back when a login reached `healthy`. */
const AWAITING_SCAN = "请在打开的运营浏览器中扫码登录。";
/** T109 mapped an abandoned lease to this; nobody had run the real App path. */
const RECOVERY_TEXT =
  "上一次运营浏览器没有正常收尾。请先执行「安全注销」，再重新登录一次抖音。";
const RECOVERY_CODE = "故障代码：profile_recovery_required";
/** The one sentence this whole path used to collapse into. */
const RETRY_LATER = "请稍后重试";
const GENERIC_INTERNAL = "重新操作不会有效";

const phase = process.env.AUTOMATION_TOOL_T114_PHASE;

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (value === undefined || value.length === 0) {
    throw new Error(`Missing T114 environment value: ${name}`);
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

async function openPlatformPage(): Promise<void> {
  await browser.$("li=平台状态").click();
  await expect(await browser.$("h2")).toHaveText("平台状态");
}

/**
 * Wait for one settled outcome of a platform button.
 *
 * A bare `waitUntil` on the wanted sentence turns every product regression into
 * the same timeout, which is exactly the complaint T109 recorded about this
 * directory. Report what the page and the authoritative projection actually
 * said instead, so one failed run is enough to name the broken branch.
 */
async function clickAndReadOutcome(label: string): Promise<string> {
  const button = await browser.$(`button=${label}`);
  await button.click();
  let latest = "";
  try {
    await browser.waitUntil(
      async () => {
        latest = await browser.$("body").getText();
        const settled = await button.isEnabled();
        return (
          settled &&
          (latest.includes(RECOVERY_TEXT) ||
            latest.includes(RETRY_LATER) ||
            latest.includes(GENERIC_INTERNAL) ||
            latest.includes(AWAITING_SCAN) ||
            latest.includes("登录正常"))
        );
      },
      { timeout: 180_000 },
    );
  } catch {
    const session = await browser.tauri.execute(({ core }) =>
      core.invoke("get_douyin_platform_session"),
    );
    throw new Error(
      `T114 "${label}" never settled: ${JSON.stringify({
        authoritativeSession: session,
        renderedText: latest,
      })}`,
    );
  }
  return latest;
}

/**
 * An abandoned lease must produce the one instruction that actually clears it.
 *
 * The failure message carries the rendered text because the whole point of
 * T109/T114 is that "it timed out" is not a usable answer here.
 */
function assertRecoveryGuidance(label: string, text: string): void {
  assert.ok(
    text.includes(RECOVERY_TEXT),
    `T114 "${label}" must name the safe-logout recovery. Rendered: ${text}`,
  );
  assert.ok(
    text.includes(RECOVERY_CODE),
    `T114 "${label}" must surface the fault code. Rendered: ${text}`,
  );
  assert.ok(
    !text.includes(RETRY_LATER),
    `T114 "${label}" must not tell the operator to retry a permanently failing action. Rendered: ${text}`,
  );
}

describe("T114 abandoned operations-profile lease recovery", () => {
  it("blocks both buttons after a killed App and recovers through safe logout", async () => {
    await waitForStartup();

    if (phase === "abandon") {
      const preparation = (await browser.tauri.execute(({ core }) =>
        core.invoke("prepare_platform_session_reuse_for_acceptance"),
      )) as Preparation;
      assert.match(preparation.installationId, UUID_V4);

      await openPlatformPage();
      await browser.waitUntil(
        async () => (await browser.$("body").getText()).includes("尚未确认"),
        { timeout: 60_000, timeoutMsg: "real Control Plane Session query did not render" },
      );

      // `awaiting_scan` is not `healthy`, so the App keeps the operations
      // profile leased and the browser open, which is the state the operator is
      // in whenever they are told to go and scan a code.
      const text = await clickAndReadOutcome("打开登录处理");
      assert.ok(
        text.includes(AWAITING_SCAN),
        `T114 setup needs a held lease, i.e. a non-healthy login. Rendered: ${text}`,
      );

      const appProcessId = (await browser.tauri.execute(({ core }) =>
        core.invoke("app_process_id_for_acceptance"),
      )) as number;
      assert.ok(Number.isSafeInteger(appProcessId) && appProcessId > 1);
      await signal(requiredEnvironment("AUTOMATION_TOOL_T114_LEASE_HELD_SIGNAL"), {
        appProcessId,
        installationId: preparation.installationId,
      });

      // The runner hard-kills this App; a graceful exit would release the lease
      // and destroy the very state under test.
      await browser.waitUntil(() => false, {
        timeout: 240_000,
        timeoutMsg: "T114 runner did not kill the App holding the lease",
      });
      return;
    }

    if (phase !== "blocked") {
      throw new Error(`Unsupported T114 phase: ${phase ?? "missing"}`);
    }

    await openPlatformPage();
    await browser.waitUntil(
      async () => (await browser.$("body").getText()).length > 0,
      { timeout: 60_000, timeoutMsg: "T114 platform page did not render" },
    );

    // Both buttons the operator is offered must fail the same, nameable way.
    assertRecoveryGuidance("我已处理，重新检查", await clickAndReadOutcome("我已处理，重新检查"));
    assertRecoveryGuidance("打开登录处理", await clickAndReadOutcome("打开登录处理"));

    // The only escape hatch the product offers. If this does not work the
    // operator is permanently locked out, so it is part of the same contract.
    const logout = await browser.$("button=安全注销");
    assert.equal(await logout.isEnabled(), true);
    await logout.click();
    await browser.$("button=确认注销").click();
    let afterLogout = "";
    try {
      await browser.waitUntil(
        async () => {
          afterLogout = await browser.$("body").getText();
          return afterLogout.includes("需要登录") && (await logout.isEnabled());
        },
        { timeout: 180_000 },
      );
    } catch {
      const session = await browser.tauri.execute(({ core }) =>
        core.invoke("get_douyin_platform_session"),
      );
      throw new Error(
        `T114 safe logout did not clear the abandoned lease: ${JSON.stringify({
          authoritativeSession: session,
          renderedText: afterLogout,
        })}`,
      );
    }

    await signal(requiredEnvironment("AUTOMATION_TOOL_T114_RECOVERED_SIGNAL"), {
      recovered: true,
    });
    assert.ok(existsSync(requiredEnvironment("AUTOMATION_TOOL_T114_RECOVERED_SIGNAL")));

    await browser.tauri.execute(({ core }) => core.invoke("exit_app_for_acceptance"));
  });
});
