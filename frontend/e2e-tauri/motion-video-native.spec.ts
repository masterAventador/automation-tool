import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { browser, expect } from "@wdio/globals";

const SUBJECT = "BM08 品牌增长验证";
const BEATS = [
  ["增长看得见", "字幕：本周销售增长 38%"],
  ["续费驱动增长", "字幕：客户持续选择新版"],
  ["立即体验新版", "字幕：现在开始下一步行动"],
] as const;

async function openMotionStudio() {
  await browser
    .$("//li[contains(@class,'ant-menu-item') and .//*[normalize-space()='工作台']]")
    .click();
  await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");
  await browser
    .$("//li[contains(@class,'ant-menu-item') and .//*[normalize-space()='视频制作']]")
    .click();
  const studio = await browser.$("section[aria-label='视频制作工作区']");
  await expect(studio).toBeDisplayed();
  await studio.$("button[aria-label='选择品牌动效成片']").click();
  return studio;
}

async function confirmVisibleAction(titleFragment: string) {
  // Ant Design keeps dismissed popups mounted but hidden, and entrance and
  // leave animations never finish inside the hidden test window (WebKit does
  // not deliver animation frames there), so popup buttons never report as
  // displayed. Scope by the popconfirm title and click its primary button
  // by existence instead.
  const confirm = await browser.$(
    `//div[contains(@class,'ant-popconfirm') and not(contains(@class,'ant-popover-hidden'))` +
      ` and contains(., '${titleFragment}')]//button[contains(@class,'ant-btn-primary')]`,
  );
  await confirm.waitForExist({
    timeout: 10_000,
    timeoutMsg: `open confirm popup for “${titleFragment}” never appeared`,
  });
  await confirm.click();
}

describe("BM-08 production App native brand-motion acceptance", () => {
  it("edits, previews, cancels, renders, plays and deletes a real MP4", async () => {
    const evidenceVideo = process.env.AUTOMATION_TOOL_BM08_EVIDENCE_VIDEO;
    assert.ok(evidenceVideo, "BM-08 acceptance evidence output path must be injected");
    const studio = await openMotionStudio();

    const title = await studio.$("input[aria-label='视频标题']");
    await expect(title).toBeEnabled();
    await title.setValue(SUBJECT);
    await expect(studio).toHaveText(expect.stringContaining("固定模板手工制作"));
    await expect(studio).toHaveText(expect.stringContaining("当前没有调用视频创作模型"));
    assert.doesNotMatch(await studio.getText(), /网址|URL|抓取/);

    await studio.$("div[role='tab']=脚本与分镜").click();
    for (let index = 0; index < BEATS.length; index += 1) {
      await studio
        .$(`input[aria-label='第 ${index + 1} 段标题']`)
        .setValue(BEATS[index]![0]);
      await studio
        .$(`input[aria-label='第 ${index + 1} 段字幕']`)
        .setValue(BEATS[index]![1]);
    }

    await studio.$("div[role='tab']=制作设置").click();
    const professionalBlue = await studio.$(
      "div[role='radio'][aria-label='专业蓝']",
    );
    await expect(professionalBlue).toBeDisplayed();
    await professionalBlue.click();
    await studio.$("input[aria-label='品牌主色']").setValue("#1234ab");
    await studio.$("input[aria-label='品牌辅助色']").setValue("#f2eadb");

    const logoBytes = readFileSync(resolve(process.cwd(), "src-tauri/icons/128x128.png"))
      .toString("base64");
    await browser.execute(
      (encodedLogo: string) => {
        const input = document.querySelector<HTMLInputElement>(
          "input[aria-label='品牌 Logo 文件']",
        );
        if (input === null) throw new Error("brand logo input is missing");
        const raw = globalThis.atob(encodedLogo);
        const bytes = Uint8Array.from(raw, (character) => character.charCodeAt(0));
        const transfer = new DataTransfer();
        transfer.items.add(new File([bytes], "bm08-logo.png", { type: "image/png" }));
        Object.defineProperty(input, "files", {
          configurable: true,
          value: transfer.files,
        });
        input.dispatchEvent(new Event("change", { bubbles: true }));
      },
      logoBytes,
    );
    await expect(await studio.$("img[alt='品牌 Logo 预览']")).toBeDisplayed();
    await expect(studio).toHaveText(expect.stringContaining("已选择风格：专业蓝"));

    await studio.$("div[role='tab']=预览").click();
    const preview = await studio.$("section[aria-label='品牌动效播放预览']");
    await expect(preview).toBeDisplayed();
    await expect(preview).toHaveText(expect.stringContaining(BEATS[0][0]));
    await expect(await preview.$("img[alt='品牌动效 Logo']")).toBeDisplayed();
    const progressColor = await browser.execute(() => {
      const element = document.querySelector<HTMLElement>(".motion-playback-progress");
      if (element === null) throw new Error("motion preview progress is missing");
      return globalThis.getComputedStyle(element).backgroundColor;
    });
    assert.equal(progressColor, "rgb(18, 52, 171)");
    await studio.$("button=播放预览").click();
    await browser.waitUntil(
      async () => (await preview.getText()).includes("第 2 段 / 3"),
      { timeout: 5_000, timeoutMsg: "native preview never advanced to beat 2" },
    );
    await browser.waitUntil(
      async () => (await preview.getText()).includes("第 3 段 / 3"),
      { timeout: 5_000, timeoutMsg: "native preview never advanced to beat 3" },
    );
    await expect(preview).toHaveText(expect.stringContaining(BEATS[2][1]));

    // First run proves cancellation travels through the real App command,
    // checkpoint and running Chromium worker.
    await studio.$("button=提交本机渲染").click();
    await expect(studio).toHaveText(expect.stringContaining("已提交真实本机渲染任务"));
    await studio.$("div[role='tab']=制作任务").click();
    const cancel = await studio.$("button[aria-label='取消品牌动效任务']");
    await expect(cancel).toBeDisplayed();
    await cancel.click();
    await confirmVisibleAction("确定取消这个品牌动效任务吗");
    await browser.waitUntil(
      async () => (await studio.getText()).includes("已取消"),
      { timeout: 15_000, timeoutMsg: "cancelled motion checkpoint never reached the App" },
    );

    // The cancelled worker exits cooperatively only after it observes the
    // marker behind the deliberately slowed acceptance browser, so the
    // single-worker orchestrator can stay busy for several seconds. Keep
    // retrying through the real recovery path the App shows for that case
    // until the second real submission is accepted.
    await studio.$("div[role='tab']=预览").click();
    await browser.waitUntil(
      async () => {
        await studio.$("button=提交本机渲染").click();
        await browser.pause(2_000);
        return !(await studio.getText()).includes("本机渲染组件暂时不可用");
      },
      { timeout: 60_000, interval: 500, timeoutMsg: "second real submission was never accepted" },
    );
    await studio.$("div[role='tab']=制作任务").click();
    await browser.waitUntil(
      async () => {
        const text = await studio.getText();
        if (text.includes("制作失败")) {
          throw new Error(`real BM-08 render failed:\n${text}`);
        }
        return text.includes("已完成");
      },
      { timeout: 120_000, interval: 1_000, timeoutMsg: "real motion MP4 did not complete" },
    );
    // Ant Design replaces the percent text with a check icon at 100%, so
    // assert the success state of the progress bar itself.
    const succeededProgressBars = await browser.execute(
      () => document.querySelectorAll(".ant-progress-status-success").length,
    );
    assert.ok(
      succeededProgressBars >= 1,
      "succeeded job progress bar never reached the success state",
    );

    await studio.$("div[role='tab']=成片").click();
    const play = await studio.$(`button[aria-label='播放${SUBJECT}']`);
    await expect(play).toBeDisplayed();
    await play.click();
    const player = await studio.$(`video[aria-label='${SUBJECT}成片播放器']`);
    await expect(player).toBeDisplayed();
    await browser.waitUntil(
      async () =>
        browser.execute(() => {
          const video = document.querySelector<HTMLVideoElement>(
            "video[aria-label='BM08 品牌增长验证成片播放器']",
          );
          return (
            video !== null &&
            video.error === null &&
            video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA &&
            Number.isFinite(video.duration) &&
            video.duration >= 2.9 &&
            video.duration <= 3.1 &&
            (video.currentTime > 0 || video.ended || video.played.length > 0)
          );
        }),
      { timeout: 30_000, interval: 250, timeoutMsg: "App player did not decode and play the MP4" },
    );
    const source = await player.getAttribute("src");
    assert.ok(source !== null && source.startsWith("data:video/mp4;base64,"));
    const encoded = source.slice("data:video/mp4;base64,".length);
    const bytes = Buffer.from(encoded, "base64");
    assert.ok(bytes.length > 10_000, "rendered MP4 is unexpectedly small");
    writeFileSync(evidenceVideo, bytes, { flag: "wx" });

    await studio.$("button=删除成片").click();
    await confirmVisibleAction("删除后无法恢复");
    await browser.waitUntil(
      async () => (await studio.getText()).includes("还没有已导入的成片"),
      { timeout: 15_000, timeoutMsg: "deleted Artifact remained visible in the App" },
    );
  });
});
