import { describe, expect, it } from "vitest";

import contract from "../../../../contracts/video/motion-authoring-model-call.v1.json";
import {
  MOTION_AUTHORING_IDLE_WAIT_SECONDS,
  MOTION_THINKING,
  motionThinkingNotice,
} from "./motion-model-call";

describe("motion authoring model call limits", () => {
  it("takes the idle wait from the shared contract", () => {
    expect(MOTION_AUTHORING_IDLE_WAIT_SECONDS).toBe(contract.streamIdleTimeoutSeconds);
  });
});

/**
 * 深度思考的开关，和它要付的时间。
 *
 * 视频创作模型答之前会先推理，而推理那一段就是等待的大头——2026-07-28 拿真实
 * 编排 prompt 对着真实模型各跑三次：开着 41.7 秒（40.5~51.0），关掉 10.9 秒
 * （8.5~23.5）。用户要能自己选，但选之前得先看见这笔账。
 */
describe("深度思考", () => {
  it("两个耗时都来自共享契约，不在界面里另写一份", () => {
    expect(MOTION_THINKING.secondsWithThinking).toBe(
      contract.thinking.measuredSecondsWithThinking,
    );
    expect(MOTION_THINKING.secondsWithoutThinking).toBe(
      contract.thinking.measuredSecondsWithoutThinking,
    );
    expect(MOTION_THINKING.defaultEnabled).toBe(contract.thinking.defaultEnabled);
  });

  /**
   * 默认保持开着。
   *
   * 量到的只有时间，没有量质量。为省半分钟就让每条片子悄悄变差，不是这一侧
   * 能替操作者做的取舍——所以默认不动，只是把选择权和账单一起交出去。
   */
  it("默认开着，因为只量了时间没量质量", () => {
    expect(MOTION_THINKING.defaultEnabled).toBe(true);
  });

  it("两句文案都说清这一档要多久、和另一档差多少", () => {
    const on = motionThinkingNotice(true);
    const off = motionThinkingNotice(false);

    expect(on).toContain(`${MOTION_THINKING.secondsWithThinking} 秒`);
    expect(off).toContain(`${MOTION_THINKING.secondsWithoutThinking} 秒`);
    // 差值必须自己算出来，不能是文案里另写的数——两处各写一份就会各改各的。
    const saved =
      MOTION_THINKING.secondsWithThinking - MOTION_THINKING.secondsWithoutThinking;
    expect(off).toContain(`${saved} 秒`);
    // 时间之外的代价也要说：只量过时间，没量过质量。
    expect(on + off).toMatch(/质量|效果|周全|推敲/);
  });

  it("文案不写没量过的精度", () => {
    expect(motionThinkingNotice(true)).not.toMatch(/\d+\.\d/);
    expect(motionThinkingNotice(false)).not.toMatch(/\d+\.\d/);
  });
});
