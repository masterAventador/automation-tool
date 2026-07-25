import { describe, expect, it } from "vitest";

import contract from "../../../../contracts/video/motion-storyboard-duration.v1.json";
import {
  MOTION_DURATION_LIMITS,
  motionDurationProblem,
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
});
