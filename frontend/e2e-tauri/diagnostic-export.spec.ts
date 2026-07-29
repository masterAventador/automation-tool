import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";
import {
  openWorkbenchSection,
  waitForStartup,
} from "./navigation";

describe("H8-13 hidden App diagnostic export acceptance", () => {
  it("exports only after the user confirms in 设置与诊断", async () => {
    await waitForStartup();
    await openWorkbenchSection("设置");
    await expect(await browser.$("h2")).toHaveText("设置");

    const body = await browser.$("body");
    assert.ok((await body.getText()).includes("导出诊断包"));
    assert.ok((await body.getText()).includes("确认导出"));
    assert.ok((await body.getText()).includes("不会上传"));
    assert.ok((await body.getText()).includes("系统下载目录"));
    const confirmButton = await browser.$("#confirm-diagnostic-export");
    await confirmButton.scrollIntoView({ block: "center", inline: "center" });
    await confirmButton.waitForClickable();
    await expect(confirmButton).toBeDisplayed();
    const receipt = await browser.executeAsync((done) => {
      const internal = window as typeof window & {
        __TAURI_INTERNALS__: { invoke(command: string): Promise<unknown> };
      };
      void internal.__TAURI_INTERNALS__.invoke("export_diagnostics").then(
        (value) => done({ ok: true, value }),
        (error: unknown) => done({ error, ok: false }),
      );
    });
    const result = receipt as { ok: boolean; value?: unknown };
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.match(
      String((result.value as { fileName?: unknown }).fileName),
      /^automation-tool-diagnostics-[0-9a-f-]+\.zip$/,
    );
    const bodyText = await body.getText();
    assert.doesNotMatch(bodyText, /\/Users\/|\/home\/|[A-Z]:\\|private-native-secret/i);
  });
});
