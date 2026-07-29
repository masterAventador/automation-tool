import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";
import {
  openVideoEditing,
  openWorkbenchSection,
  waitForStartup,
} from "./navigation";

const SOURCE_ARTIFACT = "9f48954d-2df1-4168-8f33-b62c5772845b";

describe("VE-03 production App standalone video editing acceptance", () => {
  it("creates a project, edits the timeline, saves revisions and shows honest submission state", async () => {
    await waitForStartup();

    // 改版后「轻量剪辑」和两种成片方式并列在「创作」下的分段控件里，
    // 不再是独立的左侧入口。
    await openVideoEditing();

    const workbench = await browser.$("section[aria-label='视频剪辑工作区']");
    await expect(workbench).toBeDisplayed();
    await expect(workbench).toHaveText(expect.stringContaining("还没有剪辑项目"));

    // 创建剪辑项目。
    await workbench.$("input[aria-label='剪辑项目标题']").setValue("发布会精剪");
    await workbench.$("textarea[aria-label='输入素材引用']").setValue(SOURCE_ARTIFACT);
    await workbench.$("button=创建剪辑项目").click();
    await expect(workbench).toHaveText(
      expect.stringContaining("已创建剪辑项目：发布会精剪"),
    );

    // 打开时间轴编辑：默认一条画面轨道，素材引用来自项目输入素材。
    await workbench.$("button=打开时间轴编辑").click();
    await expect(workbench).toHaveText(expect.stringContaining("正在编辑：发布会精剪"));
    await expect(workbench).toHaveText(expect.stringContaining("画面轨道 1"));
    assert.equal(
      await workbench.$("input[aria-label='轨道1片段1素材引用']").getValue(),
      SOURCE_ARTIFACT,
    );

    // 轨道与片段编辑：字幕轨道、转场和片段时长。
    await workbench.$("button=添加字幕轨道").click();
    await expect(workbench).toHaveText(expect.stringContaining("字幕轨道 2"));
    await workbench
      .$("input[aria-label='轨道2片段1字幕文字']")
      .setValue("欢迎来到发布会");
    await workbench
      .$("select[aria-label='轨道1片段1转场']")
      .selectByAttribute("value", "fade");

    // 保存两次，修订号单调递增。
    await workbench.$("button=保存时间轴").click();
    await expect(workbench).toHaveText(expect.stringContaining("已保存修订：第 1 版"));
    await workbench.$("button=保存时间轴").click();
    await expect(workbench).toHaveText(expect.stringContaining("已保存修订：第 2 版"));

    // 预览只展示真实保存的时间轴结构，不冒充视频画面。
    await workbench.$("div[role='tab']=预览").click();
    await expect(workbench).toHaveText(expect.stringContaining("时间轴结构预览"));
    await expect(workbench).toHaveText(expect.stringContaining("轨道 1（画面）"));
    await expect(workbench).toHaveText(expect.stringContaining("轨道 2（字幕）"));
    await expect(workbench).toHaveText(
      expect.stringContaining("视频画面预览将在云端剪辑服务接入后提供。"),
    );

    // 提交入口保持真实不可用状态，任务列表没有假数据。
    await workbench.$("div[role='tab']=提交与任务").click();
    await expect(workbench).toHaveText(expect.stringContaining("还没有剪辑任务"));
    await workbench.$("button=提交剪辑任务").click();
    await expect(workbench).toHaveText(
      expect.stringContaining("云端剪辑功能尚未开通"),
    );
    await expect(workbench).toHaveText(expect.stringContaining("还没有剪辑任务"));

    const body = await browser.$("body").getText();
    assert.doesNotMatch(body, /moneyprinter|hyperframes|b-roll/i);
    assert.doesNotMatch(body, /aliyun|阿里云|tencent|腾讯云|provider|ims|ice/i);
    assert.doesNotMatch(body, /完成 100%|示例成片|假任务/);

    // 回到助理页，避免把页面状态遗留给后续验收用例——常驻 App 在多个 spec
    // 之间是共享的。`waitForStartup` 只等不导航，用它做这件事等于没做。
    await openWorkbenchSection("AI 助理");
  });
});
