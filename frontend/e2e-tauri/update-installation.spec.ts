import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

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
    { interval: 100, timeout: 20_000, timeoutMsg: `update did not reach ${state}/${action}` },
  );
  return readState();
}

async function decide(decision: "defer" | "skip_version" | "install_now"): Promise<UpdateState> {
  return (await browser.tauri.execute(({ core }, selected) =>
    core.invoke("decide_app_update", { decision: selected }),
  decision)) as UpdateState;
}

describe("H8-21 hidden App installation coordination acceptance", () => {
  it("uses the original App startup and decision entrypoints", async () => {
    await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");
    const scenario = process.env.H821_SCENARIO;
    assert.deepEqual(await browser.tauri.listWindows(), ["main"]);

    if (scenario === "optional") {
      const initial = await waitForState("ready", "prompt");
      assert.equal(initial.release?.version, "0.2.0");
      assert.equal(initial.release?.policy, "optional");
      assert.equal((await decide("defer")).action, "deferred");
      assert.equal(
        ((await browser.tauri.execute(({ core }) =>
          core.invoke("check_app_update_now"),
        )) as UpdateState).action,
        "prompt",
      );
      assert.equal((await decide("skip_version")).action, "skipped");
      assert.equal(
        ((await browser.tauri.execute(({ core }) =>
          core.invoke("check_app_update_now"),
        )) as UpdateState).action,
        "suppressed",
      );
      const newer = (await browser.tauri.execute(({ core }) =>
        core.invoke("check_app_update_now"),
      )) as UpdateState;
      assert.equal(newer.state, "ready");
      assert.equal(newer.action, "prompt");
      assert.equal(newer.release?.version, "0.3.0");
      const launched = await decide("install_now");
      assert.equal(launched.state, "installation_launched");
      assert.equal(launched.release?.version, "0.3.0");
      return;
    }

    if (scenario === "forced-first") {
      const ready = await waitForState("ready", "forced");
      assert.equal(ready.release?.version, "0.2.0");
      assert.equal(ready.release?.policy, "forced");
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
