import { describe, expect, it } from "vitest";

import contract from "../../../../contracts/video/motion-one-sentence-brief.v1.json";
import durationContract from "../../../../contracts/video/motion-storyboard-duration.v1.json";
import {
  MOTION_BRIEF_FILM_SECONDS,
  MOTION_BRIEF_LIMITS,
  motionBriefProblem,
} from "./motion-one-sentence";

/**
 * 一句话入口的每一条边界都必须来自共享契约。
 *
 * 这个表单在 App 里，判它的编排代理在另一个进程、另一门语言里。边界一旦各写一份，
 * 表单就会提供代理会拒绝的选项，或者拦下代理本来接受的输入，而两边都看不见分歧。
 */
describe("one-sentence motion brief limits", () => {
  it("takes every bound from the shared contracts", () => {
    expect(MOTION_BRIEF_LIMITS.maxBriefCharacters).toBe(contract.maxBriefCharacters);
    expect(MOTION_BRIEF_LIMITS.aspectRatios).toEqual(contract.aspectRatios);
    expect(MOTION_BRIEF_LIMITS.languages).toEqual(contract.languages);
    // 片长上限不在这份契约里：它已经在分镜时长契约里声明过一次，
    // 再写一遍就是这份契约本身要避免的第二个来源。
    expect(MOTION_BRIEF_LIMITS.durationSecondsMaximum).toBe(
      durationContract.totalSecondsMaximum,
    );
  });

  it("accepts a sentence inside every bound", () => {
    expect(motionBriefProblem("用蓝色商务风做一段本周销售增长说明", 12)).toBeNull();
  });

  it("explains an empty sentence instead of submitting nothing", () => {
    expect(motionBriefProblem("   ", 12)).toBe("请先用一句话描述你想要的视频内容。");
  });

  it("explains a sentence past the contract length", () => {
    const problem = motionBriefProblem("测".repeat(contract.maxBriefCharacters + 1), 12);
    expect(problem).toBe(
      `一句话描述最多 ${contract.maxBriefCharacters} 个字，请精简后再提交。`,
    );
  });

  it("explains a film the local renderer cannot capture", () => {
    const problem = motionBriefProblem(
      "用蓝色商务风做一段本周销售增长说明",
      durationContract.totalSecondsMaximum + 1,
    );
    expect(problem).toBe(
      `本机最长可以制作 ${durationContract.totalSecondsMaximum} 秒的视频，请调短片长。`,
    );
  });

  it("explains a film shorter than one second", () => {
    expect(motionBriefProblem("用蓝色商务风做一段说明", 0)).toBe(
      "片长至少 1 秒，请调长片长。",
    );
  });
});

/**
 * 一句话入口没有片长控件，成片长度是固定的。
 *
 * 用户此前就为「每段 1 秒写死」抱怨过一次，那条已经改成可配；这个入口又把总时长
 * 写死了一遍。写死本身在首期是可以接受的取舍，但**不告诉用户**不行：客户说
 * 「做一个三分钟的产品介绍」，系统安静地做出十几秒的片子、全程不提示，
 * 在演示现场比少一个功能更难看。所以这个数字必须只有一个来源，界面照它说。
 */
describe("one-sentence film length", () => {
  it("is the storyboard default, taken from the shared duration contract", () => {
    expect(MOTION_BRIEF_FILM_SECONDS).toBe(
      durationContract.beatCountDefault * durationContract.secondsPerBeatDefault,
    );
  });

  it("is a length the same entry would accept", () => {
    expect(motionBriefProblem("用蓝色商务风做一段本周销售增长说明", MOTION_BRIEF_FILM_SECONDS))
      .toBeNull();
  });
});
