import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

describe("desktop workbench", () => {
  it("opens the no-login workbench in the real Tauri main window", async () => {
    const heading = await browser.$("h2");
    await expect(heading).toHaveText("RPA 运营工作台");

    const windows = await browser.tauri.listWindows();
    assert.deepEqual(windows, ["main"]);

    const bodyText = await browser.$("body").getText();
    assert.doesNotMatch(bodyText, /产品登录|注册账号|账号登录/);
  });
});
