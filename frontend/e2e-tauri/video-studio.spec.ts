import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";
import {
  openMaterialVideoStudio,
  waitForStartup,
} from "./navigation";

describe("VF-06 production App video studio acceptance", () => {
  it("opens every video page from the normal left navigation without fake results", async () => {
    // The embedded Tauri service keeps one App alive across WDIO workers.
    // Reload the production frontend so this file owns its initial state.
    await browser.refresh();
    await waitForStartup();

    const studio = await openMaterialVideoStudio();
    // 2026-07-29: this used to assert that `打开完整制作界面` renders greyed out
    // until a method is chosen. That button no longer exists in the App at all —
    // `OperationsWorkspace` is the only place that renders `VideoStudio`, and it
    // passes `embedded` unconditionally, which replaces the whole opener with the
    // notice below. Asserting the old shape was asserting a screen the product
    // stopped having.
    assert.equal(await studio.$("button=打开完整制作界面").isExisting(), false);
    await expect(studio).toHaveText(
      expect.stringContaining(
        "完整制作流程将直接嵌入当前 App，不会打开额外窗口。当前真实内嵌服务尚未接入。",
      ),
    );

    const pages = [
      ["脚本与分镜", "脚本与分镜尚未生成"],
      ["制作设置", "尚未选择制作方式"],
      ["预览", "还没有可预览内容"],
      ["制作任务", "还没有真实制作任务"],
      ["成片", "还没有已导入的成片"],
    ] as const;
    for (const [tab, expected] of pages) {
      await studio.$(`div[role='tab']=${tab}`).click();
      await expect(studio).toHaveText(expect.stringContaining(expected));
    }

    const body = await browser.$("body").getText();
    assert.doesNotMatch(body, /moneyprinter|hyperframes|b-roll/i);
    assert.doesNotMatch(body, /完成 100%|示例成片|假任务/);
  });
});
