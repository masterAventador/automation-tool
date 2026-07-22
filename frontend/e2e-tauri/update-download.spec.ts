import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

interface FailedUpdateState {
  readonly state: "failed";
  readonly stage: string;
  readonly code: string;
  readonly retryable: boolean;
}

interface ReadyUpdateState {
  readonly state: "ready";
  readonly release: {
    readonly version: string;
    readonly channel: string;
    readonly policy: string;
    readonly artifact: {
      readonly target: string;
      readonly arch: string;
      readonly sha256: string;
      readonly sizeBytes: number;
    };
  };
}

type UpdateState = FailedUpdateState | ReadyUpdateState | { readonly state: string };

async function readState(): Promise<UpdateState> {
  return (await browser.tauri.execute(({ core }) =>
    core.invoke("get_app_update_state"),
  )) as UpdateState;
}

describe("H8-20 hidden App update download acceptance", () => {
  it("recovers the startup download through the same manual App command", async () => {
    await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");
    await browser.waitUntil(async () => (await readState()).state === "failed", {
      interval: 100,
      timeout: 20_000,
      timeoutMsg: "startup update check did not reach the expected retryable failure",
    });
    assert.deepEqual(await readState(), {
      state: "failed",
      stage: "download",
      code: "transport_unavailable",
      retryable: true,
    });

    const resumed = (await browser.tauri.execute(({ core }) =>
      core.invoke("check_app_update_now"),
    )) as UpdateState;
    assert.equal(resumed.state, "ready");
    const ready = resumed as ReadyUpdateState;
    assert.equal(ready.release.version, "0.2.0");
    assert.equal(ready.release.channel, "stable");
    assert.equal(ready.release.policy, "optional");
    assert.equal(ready.release.artifact.sizeBytes, 4);
    assert.doesNotMatch(JSON.stringify(ready), /url|signature|path|https?:/i);

    const exactCacheHit = (await browser.tauri.execute(({ core }) =>
      core.invoke("check_app_update_now"),
    )) as UpdateState;
    assert.equal(exactCacheHit.state, "ready");
    assert.deepEqual(await readState(), exactCacheHit);
    assert.deepEqual(await browser.tauri.listWindows(), ["main"]);
  });
});
