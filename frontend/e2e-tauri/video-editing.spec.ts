import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";
import { openVideoEditing, waitForStartup } from "./navigation";

interface VideoEditingPreparation {
  readonly installationId: string;
  readonly materialId: string;
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
// A cold Windows install verifies roughly 519 MB of packaged Executor/browser bytes.
const startupTimeout = process.platform === "win32" ? 360_000 : 120_000;

describe("LE-17 production App local-video editing acceptance", () => {
  it("creates, saves and renders a controlled material into a real Artifact", async () => {
    await waitForStartup({ timeout: startupTimeout });
    const preparation = (await browser.tauri.execute(({ core }) =>
      core.invoke("prepare_video_editing_for_acceptance"),
    )) as VideoEditingPreparation;
    assert.match(preparation.installationId, UUID_V4);
    assert.match(preparation.materialId, UUID_V4);

    await openVideoEditing();
    const workbench = await browser.$("section[aria-label='视频剪辑工作区']");
    await expect(workbench).toBeDisplayed();

    await workbench.$("input[aria-label='剪辑项目标题']").setValue("LE17 真实本机出片");
    await workbench.$("button=创建剪辑项目").click();
    await browser.waitUntil(
      async () => (await workbench.getText()).includes("已创建剪辑项目：LE17 真实本机出片"),
      { timeout: 30_000, timeoutMsg: "real Control Plane did not create the editing project" },
    );

    await workbench.$("div[role='tab']=时间轴编辑").click();
    await browser.waitUntil(
      async () => (await workbench.getText()).includes("尚未保存"),
      { timeout: 30_000, timeoutMsg: "the new project's timeline did not finish loading" },
    );
    const materialInput = await workbench.$("input[aria-label='轨道1片段1素材编号']");
    const durationInput = await workbench.$("input[aria-label='轨道1片段1时长毫秒']");
    const sourceInInput = await workbench.$("input[aria-label='轨道1片段1素材起点毫秒']");
    await materialInput.setValue(preparation.materialId);
    await durationInput.setValue("1000");
    await sourceInInput.setValue("0");
    assert.equal(await materialInput.getValue(), preparation.materialId);
    assert.equal(await durationInput.getValue(), "1000");
    assert.equal(await sourceInInput.getValue(), "0");
    await workbench.$("button=保存时间轴").click();
    await browser.waitUntil(
      async () => {
        const text = await workbench.getText();
        if (/时间轴还不完整|本机剪辑服务暂时不可用/.test(text)) {
          throw new Error(`the real timeline save failed: ${text}`);
        }
        return text.includes("已保存修订：第 1 版");
      },
      { timeout: 30_000, timeoutMsg: "real Control Plane did not save the timeline" },
    );

    await workbench.$("div[role='tab']=提交与任务").click();
    await workbench.$("button=提交剪辑任务").click();
    await browser.waitUntil(
      async () => {
        const text = await workbench.getText();
        if (/提交结果暂时无法确认|本机剪辑服务暂时不可用/.test(text)) {
          throw new Error(`the real editing-job submission failed: ${text}`);
        }
        return text.includes("已提交剪辑任务，正在排队。");
      },
      { timeout: 30_000, timeoutMsg: "editing job was not accepted" },
    );
    await browser.waitUntil(
      async () => {
        await workbench.$("button=刷新任务").click();
        const text = await workbench.getText();
        if (text.includes("剪辑失败")) {
          throw new Error("the production local editing job failed");
        }
        return text.includes("已完成") && text.includes("成片已入库");
      },
      {
        timeout: 180_000,
        interval: 1_000,
        timeoutMsg: "production Worker did not publish a real Artifact",
      },
    );

    const text = await workbench.getText();
    assert.match(text, /已完成/);
    assert.match(text, /成片已入库/);
    assert.doesNotMatch(text, /示例成片|假任务/);
  });
});
