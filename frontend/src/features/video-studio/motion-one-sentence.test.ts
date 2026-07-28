import { describe, expect, it } from "vitest";

import contract from "../../../../contracts/video/motion-one-sentence-brief.v1.json";
import durationContract from "../../../../contracts/video/motion-storyboard-duration.v1.json";
import {
  MOTION_BRIEF_FILM_SECONDS,
  MOTION_BRIEF_LIMITS,
  motionBriefProblem,
  motionBriefWaitEstimate,
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
    // 再写一遍就是这份契约本身要避免的第二个来源。取的是一句话入口自己那条
    // （`briefSecondsMaximum`）——`totalSecondsMaximum` 是沙箱单次捕获的上限，
    // 管的是固定模板那条路。
    expect(MOTION_BRIEF_LIMITS.durationSecondsMaximum).toBe(
      durationContract.briefSecondsMaximum,
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

  it("explains a film longer than this entry offers", () => {
    const problem = motionBriefProblem(
      "用蓝色商务风做一段本周销售增长说明",
      durationContract.briefSecondsMaximum + 1,
    );
    expect(problem).toBe(
      `本机最长可以制作 ${durationContract.briefSecondsMaximum} 秒的视频，请调短片长。`,
    );
  });

  it("explains a film shorter than one second", () => {
    expect(motionBriefProblem("用蓝色商务风做一段说明", 0)).toBe(
      "片长至少 1 秒，请调长片长。",
    );
  });
});

/**
 * 片长现在是操作者定的，这里守的是它的默认值。
 *
 * 这个入口一度把总时长写死。用户此前就为「每段 1 秒写死」抱怨过一次，那条已经
 * 改成可配，这里又写死了一遍：客户说「做一个三分钟的产品介绍」，系统安静地做出
 * 十几秒的片子、全程不提示，在演示现场比少一个功能更难看。控件补上之后写死没了，
 * 默认值还在——它必须只有一个来源，界面照它说。
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

describe("片长由操作者选择", () => {
  it("上限来自一句话入口自己的天花板，不是固定模板那条", () => {
    // `totalSecondsMaximum` 是沙箱单次捕获的上限，仍然管着固定模板那条路。
    // 一句话这条是路线 A：一个镜头一次渲染，拼起来，所以它的上限是产品决定。
    // 产品负责人 2026-07-28 定 180 秒，理由量过：原来固定 12 秒时，最短的零件
    // 也要 4.5 秒、占掉 37% 预算，模型每次都判定放不下，134 个零件在原理上够
    // 得着、实际上一个都用不上。
    expect(MOTION_BRIEF_LIMITS.durationSecondsMaximum).toBe(
      durationContract.briefSecondsMaximum,
    );
    expect(MOTION_BRIEF_LIMITS.durationSecondsMaximum).toBe(180);
    expect(MOTION_BRIEF_LIMITS.durationSecondsMaximum).toBeGreaterThan(
      durationContract.totalSecondsMaximum,
    );
  });

  it("默认值仍是原来那个，改的是能不能动它", () => {
    expect(MOTION_BRIEF_FILM_SECONDS).toBe(
      durationContract.beatCountDefault * durationContract.secondsPerBeatDefault,
    );
  });

  it("操作者选的长度会被逐条判定", () => {
    const sentence = "用蓝色商务风做一段本周销售增长说明";
    expect(motionBriefProblem(sentence, MOTION_BRIEF_LIMITS.durationSecondsMaximum)).toBeNull();
    expect(motionBriefProblem(sentence, 1)).toBeNull();
    expect(
      motionBriefProblem(sentence, MOTION_BRIEF_LIMITS.durationSecondsMaximum + 1),
    ).toContain(`${MOTION_BRIEF_LIMITS.durationSecondsMaximum} 秒`);
    expect(motionBriefProblem(sentence, 0)).not.toBeNull();
  });
});

/**
 * 把片长交给用户之前，得先让他知道这条选择要付多少时间。
 *
 * 上限从 20 秒抬到 180 秒之后，最贵的那个选项贵得不成比例：路线 A 是**一个镜头
 * 一次渲染**，每次渲染都要重新启动一遍浏览器、装载页面、等它稳定，契约里
 * `renderWallSecondsBase` 把这笔固定开销记作 30 秒。所以耗时不只随帧数涨，
 * 还随镜头数涨——180 秒的片子光启动就是几十次。一个只能选、不告诉代价的滑块，
 * 会让人顺手拉到头然后等将近一小时，中途以为卡死。
 */
describe("片长对应的等待时间", () => {
  const limits = durationContract;

  /**
   * 镜头数按**编排真正会收下的最多镜头数**算，不是另找一个数。
   *
   * 第一版拿 `secondsPerBeatDefault`（4 秒）估，而那个默认值是固定模板编辑器的，
   * 从来没进过编排 prompt——180 秒按它算 45 镜，而硬上限是 24 镜。
   * 少算一个镜头就是少算一整份 30 秒启动开销，多算同理。
   */
  it("镜头数不超过编排收得下的上限", () => {
    for (const seconds of [1, 12, 20, 60, 120, limits.briefSecondsMaximum]) {
      const { shots } = motionBriefWaitEstimate(seconds);
      expect(shots).toBe(
        Math.min(
          limits.briefBeatCountMaximum,
          Math.ceil(seconds / limits.briefSecondsPerBeatMinimum),
        ),
      );
      expect(shots).toBeLessThanOrEqual(limits.briefBeatCountMaximum);
      expect(shots).toBeGreaterThanOrEqual(1);
    }
  });

  /**
   * 钉死具体的数，不复写生产表达式。
   *
   * 上一版五条断言全是「复写同一行代数」或「松散不等式」，于是 135 秒和 150 秒
   * 因为浮点往返多报了整整一分钟，五条全绿。单调性也挡不住——多报是往上跳的。
   */
  it("每一档都算出确定的数", () => {
    // 12 秒：每镜 2 秒 → 6 镜。渲染 6×30 + 12×30×0.4 = 324 秒（取整 360），
    // 加编排上限 178 秒 = 502 秒，取整到整分钟 = 540 秒。
    expect(motionBriefWaitEstimate(12)).toEqual({
      shots: 6,
      authoringSeconds: 178,
      renderCeilingSeconds: 360,
      ceilingSeconds: 540,
    });
    // 180 秒：撞上 24 镜的硬上限。渲染 24×30 + 180×30×0.4 = 2880 秒，
    // 加 178 = 3058，取整 = 3060 秒。
    expect(motionBriefWaitEstimate(180)).toEqual({
      shots: 24,
      authoringSeconds: 178,
      renderCeilingSeconds: 2880,
      ceilingSeconds: 3060,
    });
  });

  /**
   * 「一共要等多久」和「渲染这一段该多久算不正常」是两个数。
   *
   * 编排跑完命令才返回，任务卡的计时是从**渲染开始**才起表的（`settleMotionRun`
   * 在 `.then()` 里盖时间戳）。拿含编排的总时长去当停摆参照，就等于告诉用户
   * 一个已经卡住的渲染「还正常」——多给三分钟，方向还是危险的那一边。
   */
  it("把编排那一段和渲染那一段分开报", () => {
    const estimate = motionBriefWaitEstimate(180);
    expect(estimate.authoringSeconds).toBe(178);
    expect(estimate.ceilingSeconds - estimate.renderCeilingSeconds).toBeGreaterThanOrEqual(
      estimate.authoringSeconds,
    );
    expect(estimate.renderCeilingSeconds).toBeLessThan(estimate.ceilingSeconds);
    expect(estimate.renderCeilingSeconds % 60).toBe(0);
  });

  it("整分钟报出来，别给出没有的精度", () => {
    for (let seconds = 1; seconds <= limits.briefSecondsMaximum; seconds += 1) {
      expect(motionBriefWaitEstimate(seconds).ceilingSeconds % 60).toBe(0);
    }
  });

  it("浮点往返不许把估算顶上一整分钟", () => {
    // 135 和 150 是实测踩到的两个点：除完再乘回来落在 2820.0000000000005，
    // 向上取整整整跳了一分钟。
    for (const seconds of [135, 150]) {
      const { shots, ceilingSeconds } = motionBriefWaitEstimate(seconds);
      const exact =
        178 +
        shots * limits.renderWallSecondsBase +
        (seconds * limits.framesPerSecond * limits.renderWallMillisPerFrame) / 1000;
      expect(ceilingSeconds).toBe(Math.ceil(exact / 60) * 60);
    }
  });

  it("镜头数也是成本，不只是帧数", () => {
    // 同样的总帧数，切成更多镜头就更贵——这正是要写在界面上的那件事。
    const oneShot = motionBriefWaitEstimate(2);
    const manyShots = motionBriefWaitEstimate(20);
    expect(manyShots.shots).toBeGreaterThan(oneShot.shots);
    expect(manyShots.ceilingSeconds).toBeGreaterThan(oneShot.ceilingSeconds);
  });

  it("拉到头的那个选项要贵到必须先说一声", () => {
    const longest = motionBriefWaitEstimate(limits.briefSecondsMaximum);
    // 半小时：这个量级的等待，界面不先说一声就是在坑人。
    const HALF_AN_HOUR_SECONDS = 30 * 60;
    expect(longest.ceilingSeconds).toBeGreaterThan(HALF_AN_HOUR_SECONDS);
  });

  it("片子更长，估算不会更短", () => {
    let previous = 0;
    for (let seconds = 1; seconds <= limits.briefSecondsMaximum; seconds += 1) {
      const { ceilingSeconds } = motionBriefWaitEstimate(seconds);
      expect(ceilingSeconds).toBeGreaterThanOrEqual(previous);
      previous = ceilingSeconds;
    }
  });

  it("最短的片子也至少有一个镜头", () => {
    expect(motionBriefWaitEstimate(1).shots).toBe(1);
  });
});
