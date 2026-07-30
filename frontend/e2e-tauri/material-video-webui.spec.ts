import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";
import {
  openMaterialVideoStudio,
  openWorkbenchSection,
  waitForStartup,
} from "./navigation";

const TEST_KEY = "sk-im05-invalid-desktop-key-1234567890";

describe("IM-05/IM-06 production App material video WebUI acceptance", () => {
  it("opens the real protected and product-themed WebUI through the normal product entry", async () => {
    await waitForStartup();
    await openWorkbenchSection("设置");
    const scriptSettings = await browser.$(".model-service-purpose--script");
    await scriptSettings.$("input[aria-label='文案模型服务 API Key']").setValue(TEST_KEY);
    await scriptSettings.$("button=保存配置").click();
    await expect(scriptSettings).toHaveText(expect.stringContaining("已配置"));

    const studio = await openMaterialVideoStudio();
    const mainHandle = await browser.getWindowHandle();
    assert.deepEqual(await browser.tauri.listWindows(), ["main"]);
    await studio.$("button[aria-label='选择智能素材成片']").click();
    const started = await browser.tauri.execute(({ core }) =>
      core.invoke("exercise_material_video_studio_for_acceptance"),
    );
    assert.deepEqual(started, { state: "running", failure: null });
    let embedded: unknown = null;
    await browser.waitUntil(async () => {
      embedded = await browser.tauri.execute(({ core }) =>
        core.invoke("inspect_material_video_studio_exercise_for_acceptance"),
      );
      return (embedded as { readonly state: string }).state !== "running";
    }, {
      timeout: 135_000,
      interval: 500,
      timeoutMsg: "the real child WebView acceptance probe did not finish",
    });
    assert.deepEqual(
      embedded,
      { state: "passed", failure: null },
      "the real child WebView must mount inside the sole native App window, expose the guarded form, accept subject input, preserve material settings, and close cleanly",
    );
    assert.doesNotMatch(await browser.$("body").getText(), /127\.0\.0\.1|studio-[A-Za-z0-9_-]{20}/);
    assert.deepEqual(await browser.tauri.listWindows(), ["main"]);
    assert.equal(await browser.getWindowHandle(), mainHandle);
    await studio.$("button[aria-label='选择品牌动效成片']").click();
    await browser.waitUntil(async () => {
      const snapshot = await browser.tauri.execute(({ core }) =>
        core.invoke("inspect_material_video_studio_cleanup_for_acceptance"),
      ) as {
        readonly activeWorkspace: boolean;
        readonly viewMounted: boolean;
        readonly workerStopped: boolean;
      };
      return !snapshot.viewMounted && snapshot.workerStopped && !snapshot.activeWorkspace;
    }, {
      timeout: 30_000,
      timeoutMsg: "material-video child WebView, Worker, or active workspace survived its embedded surface",
    });
    assert.deepEqual(await browser.tauri.listWindows(), ["main"]);
    await browser.$("div[role='tab']=制作任务").click();
    await expect(await browser.$("body")).toHaveText(expect.stringContaining("还没有真实制作任务"));
    await browser.$("div[role='tab']=成片").click();
    await expect(await browser.$("body")).toHaveText(expect.stringContaining("还没有已导入的成片"));

  });
});
