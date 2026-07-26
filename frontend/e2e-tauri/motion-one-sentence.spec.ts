import assert from "node:assert/strict";
import { writeFileSync } from "node:fs";

import { browser, expect } from "@wdio/globals";

/**
 * T36: the one-sentence path, driven the way a customer will drive it.
 *
 * Everything below this file has been exercised on its own — the authoring
 * agent against the real model, the render sandbox against the real embedded
 * Chromium, the still-image gate against real captured frames. None of that
 * proves the App can do it: the command, the credential lookup, the Executor
 * hand-off, the progress projection and the player are only connected here.
 *
 * The credential arrives in the environment and is typed into the real
 * settings form rather than written into the App's private files, because a
 * run that pre-seeds configuration is not acceptance of the path a user takes.
 * It is never asserted on, never logged, and never written to the evidence.
 */
const BRIEF = "用蓝色商务风做一段本周销售增长说明，三个要点";

/** Three beats of four seconds: what the form submits, from the shared contract. */
const EXPECTED_FILM_SECONDS = 12;

/** The stage names a user watches go by, in the order they must appear. */
const RUNNING_STAGES = ["准备中", "逐帧渲染中", "正在合成视频"] as const;

async function openWorkbenchSection(name: string): Promise<void> {
  await browser
    .$(`//li[contains(@class,'ant-menu-item') and .//*[normalize-space()='${name}']]`)
    .click();
}

async function openVideoStudio() {
  await openWorkbenchSection("工作台");
  await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");
  await openWorkbenchSection("视频制作");
  const studio = await browser.$("section[aria-label='视频制作工作区']");
  await expect(studio).toBeDisplayed();
  await studio.$("button[aria-label='选择品牌动效成片']").click();
  return studio;
}

describe("T36 一句话自动制作的真实 App 用户路径", () => {
  it("configures the model, refuses an empty brief, then authors, renders and plays", async function () {
    // Authoring is a real model round trip and the render captures 360 frames
    // through a real browser, so this test is minutes long by construction.
    this.timeout(1_500_000);

    const apiKey = process.env.AUTOMATION_TOOL_T36_MODEL_KEY;
    const evidenceVideo = process.env.AUTOMATION_TOOL_T36_EVIDENCE_VIDEO;
    assert.ok(apiKey, "T36 acceptance needs a real video-creation model key");
    assert.ok(evidenceVideo, "T36 acceptance needs an evidence output path");

    // Three outcomes need three different fixes and look alike from a bare wait
    // on the workbench heading: mounted, blocked by the startup gate, or still
    // checking because a probe never came back. Whatever it is, report the
    // screen — "neither" sends the next person back to reproduce it by hand.
    const startupScreen = async (): Promise<string> =>
      browser.execute(() => document.body?.innerText ?? "<empty document>");
    try {
      await browser.waitUntil(
        async () =>
          (await browser.$("h2=RPA 运营工作台").isExisting()) ||
          (await browser.$("button=打开本地修复工具").isExisting()),
        { timeout: 120_000, interval: 1_000 },
      );
    } catch {
      throw new Error(`App never left the startup check. Screen was:\n${await startupScreen()}`);
    }
    if (!(await browser.$("h2=RPA 运营工作台").isExisting())) {
      throw new Error(`App is blocked at the startup gate:\n${await startupScreen()}`);
    }

    // --- The prerequisite, through the form a user actually fills ----------
    await openWorkbenchSection("设置与诊断");
    await expect(await browser.$("h2")).toHaveText("设置与诊断");
    const videoModel = await browser.$(".model-service-purpose--video_creative");
    await expect(videoModel).toBeDisplayed();
    await videoModel.$("input[aria-label='视频创作模型服务 API Key']").setValue(apiKey);
    await videoModel.$("button=保存配置").click();
    await expect(videoModel).toHaveText(expect.stringContaining("已配置"));
    // The key must not survive anywhere the page can show it.
    assert.equal(
      await videoModel.$("input[aria-label='视频创作模型服务 API Key']").getValue(),
      "",
    );
    assert.doesNotMatch(await browser.$("body").getText(), new RegExp(apiKey));

    // --- An empty sentence is refused before anything is started ----------
    const studio = await openVideoStudio();
    await expect(await studio.$("textarea[aria-label='一句话视频需求']")).toBeDisplayed();
    // The entry has no length control, so the length has to be on the card. A
    // customer who says "make me a three minute intro" otherwise gets a much
    // shorter film with nothing anywhere saying so.
    await expect(studio).toHaveText(
      expect.stringContaining(`会生成一段 ${EXPECTED_FILM_SECONDS} 秒的视频`),
    );
    await studio.$("button=开始自动制作").click();
    await expect(studio).toHaveText(
      expect.stringContaining("请先用一句话描述你想要的视频内容。"),
    );
    await studio.$("div[role='tab']=制作任务").click();
    await expect(studio).toHaveText(expect.stringContaining("还没有真实制作任务"));
    await studio.$("div[role='tab']=新建视频").click();

    // --- One sentence, and nothing else ------------------------------------
    await studio.$("textarea[aria-label='一句话视频需求']").setValue(BRIEF);
    await studio.$("button=开始自动制作").click();
    await browser.waitUntil(
      async () => {
        const text = await studio.getText();
        if (text.includes("请先到“设置与诊断”配置视频创作模型服务。")) {
          throw new Error("the App did not find the model credential just saved");
        }
        if (text.includes("本机渲染组件暂时不可用")) {
          throw new Error("the App could not resolve the packaged render runtime");
        }
        if (text.includes("一句话自动制作暂时无法提交")) {
          throw new Error("the one-sentence submission was refused by the native command");
        }
        return text.includes("已提交一句话自动制作");
      },
      { timeout: 900_000, interval: 1_000, timeoutMsg: "one-sentence submission never landed" },
    );

    // --- The progress a user watches ---------------------------------------
    await studio.$("div[role='tab']=制作任务").click();
    const stagesSeen: string[] = [];
    const percentsSeen: number[] = [];
    await browser.waitUntil(
      async () => {
        const text = await studio.getText();
        if (text.includes("制作失败")) {
          throw new Error(`the real one-sentence render failed:\n${text}`);
        }
        for (const stage of RUNNING_STAGES) {
          if (text.includes(stage) && !stagesSeen.includes(stage)) stagesSeen.push(stage);
        }
        const percent = await browser.execute(() => {
          const bar = document.querySelector("[role='progressbar']");
          const value = bar?.getAttribute("aria-valuenow");
          return value === null || value === undefined ? null : Number(value);
        });
        if (percent !== null && percentsSeen[percentsSeen.length - 1] !== percent) {
          percentsSeen.push(percent);
        }
        return text.includes("已完成");
      },
      { timeout: 1_200_000, interval: 1_000, timeoutMsg: "the film never finished" },
    );
    assert.ok(
      stagesSeen.length >= 1,
      `no running stage was ever shown; a job that only ever reads 已完成 is not progress. saw ${JSON.stringify(stagesSeen)}`,
    );
    assert.deepEqual(
      percentsSeen,
      [...percentsSeen].sort((left, right) => left - right),
      `progress went backwards: ${JSON.stringify(percentsSeen)}`,
    );
    assert.ok(
      percentsSeen.length >= 2 && percentsSeen[percentsSeen.length - 1] === 100,
      `progress never advanced through distinct values to 100: ${JSON.stringify(percentsSeen)}`,
    );

    // --- Preview, in the App, through the existing player -------------------
    await studio.$("div[role='tab']=成片").click();
    const play = await studio.$(`button[aria-label='播放${BRIEF}']`);
    await expect(play).toBeDisplayed();
    await play.click();
    const player = await studio.$(`video[aria-label='${BRIEF}成片播放器']`);
    await expect(player).toBeDisplayed();
    await browser.waitUntil(
      async () =>
        browser.execute((expectedSeconds: number) => {
          const video = document.querySelector<HTMLVideoElement>("video[aria-label$='成片播放器']");
          return (
            video !== null &&
            video.error === null &&
            video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA &&
            Number.isFinite(video.duration) &&
            Math.abs(video.duration - expectedSeconds) < 0.2 &&
            (video.currentTime > 0 || video.ended || video.played.length > 0)
          );
        }, EXPECTED_FILM_SECONDS),
      { timeout: 60_000, interval: 250, timeoutMsg: "the App player never decoded and played the film" },
    );

    const source = await player.getAttribute("src");
    assert.ok(
      source !== null && source.startsWith("data:video/mp4;base64,"),
      "the player must read the film through the existing base64 data URL",
    );
    const bytes = Buffer.from(source.slice("data:video/mp4;base64,".length), "base64");
    assert.ok(bytes.length > 10_000, `the played film is implausibly small: ${bytes.length} bytes`);
    writeFileSync(evidenceVideo, bytes, { flag: "wx" });
  });
});
