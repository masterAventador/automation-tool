import { describe, expect, it } from "vitest";

import contract from "../../../../contracts/video/motion-storyboard-duration.v1.json";
import sandbox from "../../../../contracts/video/motion-render-sandbox-budget.v1.json";
import {
  MOTION_DURATION_LIMITS,
  motionDurationProblem,
  motionRenderCeilingSeconds,
  motionSpokenDuration,
  motionStoryboardSummary,
  resizeMotionBeats,
} from "./motion-duration";

describe("motion storyboard duration limits", () => {
  it("reads every bound from the shared contract instead of restating numbers", () => {
    expect(contract.version).toBe("motion-storyboard-duration.v1");
    expect(MOTION_DURATION_LIMITS).toEqual({
      framesPerSecond: contract.framesPerSecond,
      beatCountMinimum: contract.beatCountMinimum,
      beatCountMaximum: contract.beatCountMaximum,
      beatCountDefault: contract.beatCountDefault,
      secondsPerBeatMinimum: contract.secondsPerBeatMinimum,
      secondsPerBeatMaximum: contract.secondsPerBeatMaximum,
      secondsPerBeatDefault: contract.secondsPerBeatDefault,
      totalSecondsMaximum: contract.totalSecondsMaximum,
      briefSecondsMaximum: contract.briefSecondsMaximum,
      renderWallSecondsBase: contract.renderWallSecondsBase,
      renderWallMillisPerFrame: contract.renderWallMillisPerFrame,
    });
    expect(MOTION_DURATION_LIMITS.beatCountDefault * MOTION_DURATION_LIMITS.secondsPerBeatDefault)
      .toBeLessThanOrEqual(MOTION_DURATION_LIMITS.totalSecondsMaximum);
  });

  it("accepts a five beat, four second storyboard and reports its real length", () => {
    expect(motionDurationProblem(5, 4)).toBeNull();
    expect(motionStoryboardSummary(5, 4)).toBe("共 5 段 · 每段 4 秒 · 成片约 20 秒");
    expect(motionStoryboardSummary(1, 1)).toBe("共 1 段 · 每段 1 秒 · 成片约 1 秒");
  });

  it("explains in plain Chinese which bound a rejected storyboard broke", () => {
    const { beatCountMaximum, secondsPerBeatMaximum, totalSecondsMaximum } =
      MOTION_DURATION_LIMITS;

    expect(motionDurationProblem(0, 4)).toContain("段数");
    expect(motionDurationProblem(beatCountMaximum + 1, 1)).toContain("段数");
    expect(motionDurationProblem(3, 0)).toContain("每段时长");
    expect(motionDurationProblem(3, secondsPerBeatMaximum + 1)).toContain("每段时长");

    const overBudget = motionDurationProblem(5, 6);
    expect(overBudget).toContain(`最多 ${totalSecondsMaximum} 秒`);
    expect(overBudget).toContain("30 秒");
    expect(overBudget).not.toMatch(/[A-Za-z]{3,}/u);
  });

  it("keeps written beats when the user changes how many beats there are", () => {
    const written = [
      { title: "第一", caption: "字幕一" },
      { title: "第二", caption: "字幕二" },
      { title: "第三", caption: "字幕三" },
    ];
    const create = (index: number) => ({ title: `新第 ${index + 1} 段`, caption: "" });

    const grown = resizeMotionBeats(written, 5, create);
    expect(grown).toHaveLength(5);
    expect(grown.slice(0, 3)).toEqual(written);
    expect(grown[3]).toEqual({ title: "新第 4 段", caption: "" });

    const shrunk = resizeMotionBeats(written, 2, create);
    expect(shrunk).toEqual(written.slice(0, 2));
    expect(resizeMotionBeats(written, 3, create)).toBe(written);
  });

  /**
   * 渲染要跑多久，产品其实一直知道，只是没告诉用户。
   *
   * 契约里的 renderWallSecondsBase 和 renderWallMillisPerFrame 就是本机渲染
   * 沙箱给这条片子的时限：固定启动开销 + 每帧开销。它是**上限**不是均值——
   * rationale 写得很清楚，最长的合法片子按这个公式要 270 秒，正好压在沙箱
   * 300 秒天花板之下。界面要说的就是这个上限：到点了系统自己会停，所以没到点
   * 就不用怀疑卡死了。
   *
   * 这两个数只能从契约读。仓库因为写死的散文数字吃过亏，这里再写一遍
   * 就等于第二个事实源。
   */
  it("derives the local render ceiling from the contract instead of a written-down number", () => {
    expect(MOTION_DURATION_LIMITS.renderWallSecondsBase).toBe(
      contract.renderWallSecondsBase,
    );
    expect(MOTION_DURATION_LIMITS.renderWallMillisPerFrame).toBe(
      contract.renderWallMillisPerFrame,
    );

    const film = contract.beatCountDefault * contract.secondsPerBeatDefault;
    const frames = film * contract.framesPerSecond;
    expect(motionRenderCeilingSeconds(film)).toBe(
      contract.renderWallSecondsBase + (frames * contract.renderWallMillisPerFrame) / 1000,
    );

    // 界面承诺的上限不能超过沙箱真正会执行的那个，否则就是在保证一件沙箱会撕毁的事。
    expect(motionRenderCeilingSeconds(contract.totalSecondsMaximum)).toBeLessThanOrEqual(
      sandbox.wallClockSecondsMaximum,
    );
  });

  it("says a length the way a person reads it out", () => {
    expect(motionSpokenDuration(0)).toBe("0 秒");
    expect(motionSpokenDuration(54)).toBe("54 秒");
    expect(motionSpokenDuration(174)).toBe("2 分 54 秒");
    expect(motionSpokenDuration(180)).toBe("3 分");
  });
});
