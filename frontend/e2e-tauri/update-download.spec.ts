import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

interface UpdateRelease {
  readonly version: string;
  readonly channel: string;
  readonly policy: string;
  readonly artifact: {
    readonly target: string;
    readonly arch: string;
    readonly sha256: string;
    readonly sizeBytes: number;
  };
}

interface UpdateState {
  readonly state: string;
  readonly action?: string;
  readonly stage?: string;
  readonly code?: string;
  readonly retryable?: boolean;
  readonly release?: UpdateRelease;
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
    { interval: 100, timeout: 20_000, timeoutMsg: `update did not reach ${state}/${action}` },
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

describe("H8-20 hidden App update download acceptance", () => {
  it("recovers the interrupted startup download from the visible 设置与诊断 controls", async () => {
    await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");
    await browser.$("li=设置与诊断").click();

    // The startup check is what fails here: the feed cuts the artifact stream
    // halfway. The user is told it is retryable, not that the App is broken.
    await waitForText("暂时无法连接更新服务器，可稍后重试");
    assert.deepEqual(await readState(), {
      state: "failed",
      stage: "download",
      code: "transport_unavailable",
      retryable: true,
    });

    // Recovery is whatever the button does. That is the whole point of the
    // acceptance: the runner asserts the retry resumed at `bytes=2-` instead of
    // starting the transfer again, and only this click can ask for it.
    await browser.$("button=检查更新").click();
    await waitForText("发现新版本 0.2.0");
    const resumed = await waitForState("ready", "prompt");
    assert.equal(resumed.release?.version, "0.2.0");
    assert.equal(resumed.release?.channel, "stable");
    assert.equal(resumed.release?.policy, "optional");
    assert.equal(resumed.release?.artifact.sizeBytes, 4);
    assert.doesNotMatch(JSON.stringify(resumed), /url|signature|path|https?:/i);

    // Checking again must be served from the verified cache. The prompt covers
    // the settings card, so the user dismisses it the way the UI offers.
    await browser.$("button=稍后提醒").click();
    await waitForState("ready", "deferred");
    await browser.$("button=检查更新").click();
    await waitForText("发现新版本 0.2.0");
    const cached = await waitForState("ready", "prompt");
    assert.deepEqual(cached.release, resumed.release);

    // Scoped to the update card on purpose: 设置与诊断 also hosts service
    // settings that legitimately show endpoints, and this assertion is about
    // what the updater itself renders.
    const shown = await browser.$(".app-update-card").getText();
    assert.doesNotMatch(shown, /https?:|\/Users\/|[A-Z]:\\/);
    assert.deepEqual(await browser.tauri.listWindows(), ["main"]);
  });
});
