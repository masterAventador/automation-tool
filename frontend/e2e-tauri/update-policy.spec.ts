import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

interface UpdatePolicyRecord {
  readonly minimumVersion: string;
  readonly highestObservedVersion: string | null;
  readonly decision: string | null;
  readonly revision: number;
}

describe("H8-19 hidden App update policy acceptance", () => {
  it("initializes the production policy service in isolated AppData", async () => {
    await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");
    const record = (await browser.tauri.execute(({ core }) =>
      core.invoke("get_update_policy_record_for_acceptance"),
    )) as UpdatePolicyRecord;

    assert.deepEqual(record, {
      minimumVersion: "0.1.0",
      highestObservedVersion: null,
      decision: null,
      revision: 1,
    });
    assert.deepEqual(await browser.tauri.listWindows(), ["main"]);
  });
});
