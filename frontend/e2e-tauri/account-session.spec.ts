import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

import { workbenchIsMounted } from "./navigation";

interface AccountSessionSnapshot {
  readonly state: "unauthenticated";
  readonly account: null;
}

describe("U9-04 hidden customer Demo account gate acceptance", () => {
  it("keeps the workbench unmounted and returns only a safe native snapshot", async () => {
    await expect(await browser.$("h2")).toHaveText("登录自动化运营工具");
    const body = await browser.$("body").getText();
    // 工作台是否挂载改成问 shell 本身：改版把那个工作台标题删了，
    // 而这一句是「未登录不得进工作台」的那道闸——字符串一旦不存在，
    // 这个断言就永远成立，闸门看着在、其实已经不设防。
    assert.equal(await workbenchIsMounted(), false, "未登录不得挂载工作台");
    assert.doesNotMatch(body, /设置与诊断|atas1|atrs1/);

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
    assert.equal(await workbenchIsMounted(), false, "会话失效后不得挂载工作台");
    assert.doesNotMatch(await browser.$("body").getText(), /atas1|atrs1/);
  });
});
