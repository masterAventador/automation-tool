import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

interface AccountSessionSnapshot {
  readonly state: "unauthenticated";
  readonly account: null;
}

describe("U9-04 hidden customer Demo account gate acceptance", () => {
  it("keeps the workbench unmounted and returns only a safe native snapshot", async () => {
    await expect(await browser.$("h2")).toHaveText("登录自动化运营工具");
    const body = await browser.$("body").getText();
    assert.doesNotMatch(body, /RPA 运营工作台|设置与诊断|atas1|atrs1/);

    const result = (await browser.executeAsync((done) => {
      const internal = window as typeof window & {
        __TAURI_INTERNALS__: { invoke(command: string): Promise<unknown> };
      };
      void internal.__TAURI_INTERNALS__.invoke("restore_product_account_session").then(
        (value) => done({ ok: true, value }),
        () => done({ ok: false }),
      );
    })) as { readonly ok: boolean; readonly value?: unknown };
    assert.equal(result.ok, true);
    const snapshot = result.value as AccountSessionSnapshot;
    assert.deepEqual(snapshot, { state: "unauthenticated", account: null });

    await browser.refresh();
    await expect(await browser.$("h2")).toHaveText("登录自动化运营工具");
    assert.doesNotMatch(await browser.$("body").getText(), /RPA 运营工作台|atas1|atrs1/);
  });
});
