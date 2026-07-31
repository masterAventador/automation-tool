import assert from "node:assert/strict";
import { writeFileSync } from "node:fs";

import { browser, expect } from "@wdio/globals";
import {
  WORKBENCH_SHELL,
  openVideoStudio,
  openWorkbenchSection,
} from "./navigation";

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

/**
 * The length this run asks for, typed into the form's own control.
 *
 * Not what the film measures. A shot is as long as its line or its part's own
 * motion, whichever is longer, and the film is the sum of its shots — so this
 * number steers how much the storyboard tries to say and does not cut the
 * result. The form's own wording says exactly that; the finished length is
 * measured off the artifact by `inspect_film`.
 *
 * Deliberately longer than the twelve second default, because the default is
 * what kept the packaged parts out of every film. Measured 2026-07-28 against
 * the real model: at twelve seconds the shortest catalog part costs 37% of the
 * whole budget and the model declined every one of them — correctly, since the
 * prompt tells it a part's length is spent from the film's budget. The same
 * sentence at twenty seconds picked one to two parts. So this run both
 * exercises the new control and is the only length at which "the film really
 * used a part" can be observed at all.
 */
const EXPECTED_FILM_SECONDS = 20;

/** The stage names a user watches go by, in the order they must appear. */
const RUNNING_STAGES = ["准备中", "逐帧渲染中", "正在合成视频"] as const;

/**
 * Every way the submission can come back a failure, and what each one means.
 *
 * The previous run of this spec ended in `render_unavailable` after roughly
 * fourteen minutes, and that single code is produced at five different places
 * in the submit command — so the run proved only that something went wrong.
 * The native side now distinguishes the authoring outcomes, and this table
 * turns the card's wording back into the place it came from, so one run is
 * enough to know where the time went. The copy is matched, not the code,
 * because the card is all a user path exposes.
 */
const SUBMIT_FAILURES: readonly (readonly [string, string])[] = [
  [
    "请先到“设置与诊断”配置视频创作模型服务。",
    "configuration_required — the App did not find the model credential just saved",
  ],
  [
    "自动编排超时",
    "authoring_timed_out — the child outlived MOTION_AUTHORING_DEADLINE and was killed; the model round trip does not fit the budget",
  ],
  [
    "判定这次描述做不出来",
    "authoring_refused — the child completed the protocol and declined the request; this is the agent working, not breaking",
  ],
  [
    "自动编排没能完成",
    "authoring_crashed — the child did not write a refusal document: either it died, or it reported that the model service could not be used at all (entry.py keeps those out of the refusal channel so they stop reading as a refusal of the brief)",
  ],
  [
    "没有通过本机校验",
    "authoring_answer_invalid — the child answered and accept_authored_render_job refused the answer",
  ],
  [
    "本机渲染组件暂时不可用",
    "render_unavailable — a packaged part could not be resolved: motion_runtime_paths, seed_authoring_runtime, verified_entrypoint or the worker launch",
  ],
  [
    "一句话自动制作暂时无法提交",
    "the native command refused the submission with a code the gateway does not recognise",
  ],
];

/**
 * The workbench shell, as opposed to the startup check or the startup gate.
 *
 * The workbench heading used to be this signal and no longer exists: the redesign
 * (`c4d0d14`) replaced the single workbench heading with a per-section one, and
 * the section the App lands on — the assistant — has no heading at all. The
 * navigation landmark is what survives that, because it is the shell rather
 * than anything inside it.
 */

describe("T36 一句话自动制作的真实 App 用户路径", () => {
  it("configures the model, refuses an empty brief, then authors, renders and plays", async function () {
    // Authoring is a real model round trip and every shot is captured frame by
    // frame through a real browser, so this test is minutes long by construction.
    this.timeout(1_500_000);

    const apiKey = process.env.AUTOMATION_TOOL_T36_MODEL_KEY;
    const evidenceVideo = process.env.AUTOMATION_TOOL_T36_EVIDENCE_VIDEO;
    const evidenceShots = process.env.AUTOMATION_TOOL_T36_EVIDENCE_SHOTS;
    assert.ok(apiKey, "T36 acceptance needs a real video-creation model key");
    assert.ok(evidenceVideo, "T36 acceptance needs an evidence output path");
    assert.ok(evidenceShots, "T36 acceptance needs a shot-structure evidence output path");

    // Three outcomes need three different fixes and look alike from a bare wait
    // on the workbench heading: mounted, blocked by the startup gate, or still
    // checking because a probe never came back. Whatever it is, report the
    // screen — "neither" sends the next person back to reproduce it by hand.
    // A run that dies mid-way must say how far it got. Without these the only
    // signal is a bare "Timeout" and the next person cannot tell a slow model
    // from a stuck render from a page that never mounted.
    //
    // The elapsed time comes with it because the previous run's whole finding
    // was "about fourteen minutes, somewhere". Which step spent them is a fact
    // about this driver's own clock — it is not a detail the App reports and
    // nothing about it travels on the command wire.
    const startedAt = Date.now();
    const step = (name: string): void =>
      console.log(`[T36 step] +${Math.round((Date.now() - startedAt) / 1000)}s ${name}`);

    const startupScreen = async (): Promise<string> =>
      browser.execute(() => document.body?.innerText ?? "<empty document>");
    try {
      await browser.waitUntil(
        async () =>
          (await browser.$(WORKBENCH_SHELL).isExisting()) ||
          (await browser.$("button=打开本地修复工具").isExisting()),
        { timeout: 120_000, interval: 1_000 },
      );
    } catch {
      throw new Error(`App never left the startup check. Screen was:\n${await startupScreen()}`);
    }
    if (!(await browser.$(WORKBENCH_SHELL).isExisting())) {
      throw new Error(`App is blocked at the startup gate:\n${await startupScreen()}`);
    }

    step("workbench mounted");
    // --- The prerequisite, through the form a user actually fills ----------
    await openWorkbenchSection("设置");
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

    step("model credential saved");
    // --- An empty sentence is refused before anything is started ----------
    const studio = await openVideoStudio();
    await expect(await studio.$("textarea[aria-label='一句话视频需求']")).toBeDisplayed();
    // The length is set through the control a user has, not pre-seeded. A
    // customer who says "make me a three minute intro" used to get a much
    // shorter film with nothing anywhere saying so; now he sets it, and the
    // card restates what he set.
    const filmSeconds = await studio.$("#motion-brief-seconds");
    await filmSeconds.setValue(String(EXPECTED_FILM_SECONDS));
    await expect(studio).toHaveText(
      expect.stringContaining(`按 ${EXPECTED_FILM_SECONDS} 秒来安排内容`),
    );
    await studio.$("button=开始自动制作").click();
    await expect(studio).toHaveText(
      expect.stringContaining("请先用一句话描述你想要的视频内容。"),
    );
    await studio.$("div[role='tab']=制作任务").click();
    await expect(studio).toHaveText(expect.stringContaining("还没有真实制作任务"));
    await studio.$("div[role='tab']=新建视频").click();

    step("empty brief refused, no job created");
    // --- One real catalog choice, through the page a user sees ------------
    await studio.$("div[role='tab']=动效零件").click();
    const parts = await studio.$("section[aria-label='动效零件目录']");
    await parts
      .$("//label[contains(@class,'ant-radio-button-wrapper') and contains(., '数据与地图')]")
      .click();
    const dataChart = await parts.$(
      "//li[contains(@class,'motion-parts-card') and .//*[normalize-space()='数据图表动画']]",
    );
    await dataChart.$("button=指定给第 1 镜头").click();
    await expect(studio).toHaveText(
      expect.stringContaining("第 1 镜头：已指定数据图表动画"),
    );
    await studio.$("div[role='tab']=新建视频").click();

    step("first shot catalog override selected");
    // --- One sentence, and nothing else ------------------------------------
    await studio.$("textarea[aria-label='一句话视频需求']").setValue(BRIEF);
    // The card refuses an empty sentence with the same words it showed a moment
    // ago, so a sentence that never reached the form's state produces no visible
    // change at all — indistinguishable from a submission that never came back,
    // and the wait below would sit there for its full budget blaming the model.
    // Confirm the box holds the sentence before anything is submitted.
    assert.equal(
      await studio.$("textarea[aria-label='一句话视频需求']").getValue(),
      BRIEF,
      "the brief never reached the form, so nothing was submitted",
    );
    // Typing and submitting are marked separately from the wait that follows.
    // The previous two runs both reported "about fifteen minutes, somewhere
    // after the empty brief", and process inspection during the second one
    // found the App completely idle — no command running, no authoring child —
    // for minutes on end, which means part of that budget is spent before the
    // submission is ever made. One marker each is enough to say which.
    step("brief typed into the form");
    await studio.$("button=开始自动制作").click();
    step("submit clicked");
    // A verdict is *returned*, never thrown from inside the condition.
    //
    // `browser.waitUntil` builds a `Timer` that treats a throwing condition as
    // "not satisfied yet": it records the error and keeps ticking for the whole
    // budget, then rejects with the last one it saw. So a submission that
    // failed in seconds used to be reported by this file as
    // `submission failed after 905s` — 900s of which was this wait's own budget
    // burning down while the failure card sat on screen unchanged, and the
    // number came from the final re-evaluation rather than from the failure.
    //
    // Two runs were read as "the command takes fifteen minutes" because of it,
    // and the App was sampled twice during those minutes and found completely
    // idle, which was true and meant the opposite of what it looked like.
    // Returning the verdict makes the wait end when the App answers.
    const submission = await browser.waitUntil<string>(
      async () => {
        const text = await studio.getText();
        for (const [copy, meaning] of SUBMIT_FAILURES) {
          if (text.includes(copy)) {
            return `submission failed after ${Math.round((Date.now() - startedAt) / 1000)}s: ${meaning}`;
          }
        }
        return text.includes("已提交一句话自动制作") ? "submitted" : "";
      },
      { timeout: 900_000, interval: 1_000, timeoutMsg: "one-sentence submission never landed" },
    ).catch(async (error: Error) => {
      // Fifteen minutes of waiting must not end in a sentence that says only
      // "it didn't". Measured 2026-07-28: this timeout fired while the packaged
      // Executor, run standalone with the same request, answered in 67 seconds
      // — so the wait was not the child being slow, and nothing about the bare
      // message said where else to look. What is on screen is the one thing
      // this side can still report.
      throw new Error(
        `${error.message}. Studio showed:\n${await studio.getText()}\n---\nPage showed:\n${await startupScreen()}`,
      );
    });
    if (submission !== "submitted") {
      throw new Error(submission);
    }

    step("brief submitted, waiting on the film");
    // --- The progress a user watches ---------------------------------------
    await studio.$("div[role='tab']=制作任务").click();
    const stagesSeen: string[] = [];
    const percentsSeen: number[] = [];
    // Same rule as the submission wait: a failed render is returned, not
    // thrown, so the run ends when the job fails instead of twenty minutes
    // later with a timestamp that describes this wait rather than the render.
    const render = await browser.waitUntil<string>(
      async () => {
        const text = await studio.getText();
        if (text.includes("制作失败")) {
          return `the real one-sentence render failed after ${Math.round(
            (Date.now() - startedAt) / 1000,
          )}s:\n${text}`;
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
        return text.includes("已完成") ? "finished" : "";
      },
      { timeout: 1_200_000, interval: 1_000, timeoutMsg: "the film never finished" },
    );
    if (render !== "finished") {
      throw new Error(render);
    }
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

    // T2.2: read the product checkpoint after the user path settles. These are
    // not the answer bytes echoed back by the child: the native RenderJob owns
    // the declared table, and fills renderedFrameCount only after ffprobe has
    // decoded each encoded segment immediately before cleanup.
    const jobs: unknown = await browser.tauri.execute(({ core }) =>
      core.invoke("get_motion_render_jobs"),
    );
    assert.ok(Array.isArray(jobs) && jobs.length === 1, "T36 must settle exactly one RenderJob");
    const settled = jobs[0] as Record<string, unknown>;
    assert.equal(settled.status, "succeeded", "the retained RenderJob must be terminal success");
    assert.ok(
      Array.isArray(settled.shotStructure) && settled.shotStructure.length > 0,
      "the retained RenderJob carries no product shot table",
    );
    const shotStructure = settled.shotStructure as Array<Record<string, unknown>>;
    assert.equal(
      shotStructure.length,
      3,
      "three override slots must produce exactly three authored shots",
    );
    assert.equal(
      shotStructure[0]?.part,
      "data-chart",
      "the first-shot catalog choice did not reach the retained RenderJob",
    );
    writeFileSync(evidenceShots, JSON.stringify(settled.shotStructure), { flag: "wx" });

    step("film finished, opening the artifact");
    // --- Preview, in the App, through the existing player -------------------
    await studio.$("div[role='tab']=成片").click();
    const play = await studio.$(`button[aria-label='播放${BRIEF}']`);
    await expect(play).toBeDisplayed();
    await play.click();
    const player = await studio.$(`video[aria-label='${BRIEF}成片播放器']`);
    await expect(player).toBeDisplayed();
    await browser.waitUntil(
      async () =>
        browser.execute((minimumSeconds: number) => {
          const video = document.querySelector<HTMLVideoElement>("video[aria-label$='成片播放器']");
          return (
            video !== null &&
            video.error === null &&
            video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA &&
            Number.isFinite(video.duration) &&
            // Deliberately not `duration === the seconds the form submitted`.
            // A shot runs for whichever is longer, its line or its part's own
            // motion, and the film is the sum of its shots — the product
            // owner's correction of 2026-07-27. The submitted length steers how
            // much the storyboard tries to say; it does not cut the film. What
            // the film actually measures is asserted against the artifact by
            // `inspect_film`, with the packaged ffprobe, on the file itself.
            video.duration > minimumSeconds &&
            (video.currentTime > 0 || video.ended || video.played.length > 0)
          );
        }, 1),
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
