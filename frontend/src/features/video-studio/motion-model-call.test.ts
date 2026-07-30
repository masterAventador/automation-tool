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
  /**
   * 界面只说「省多少」，绝不说「这一步要多久」。
   *
   * 量到的 41.7 / 10.9 秒是**模型秒**（一次往返），而这张卡片上关于编排的其他话
   * 说的都是**编排整段墙钟**（`MOTION_AUTHORING_MEASURED`，T83 实测 136~178 秒）。
   * `demo-sprint-roadmap.md` 里 T92 那条专门记着这条规矩：两个口径不能互换，
   * 凭猜换一个数比留着旧数更糟。把 42 印在「编排加渲染最长约」旁边，
   * 就是把两个单位摆在同一个屏幕上。
   *
   * 差值是唯一能跨口径的数——关掉推理，从模型秒和墙钟里减掉的是同一段。
   */
  it("契约只声明「省下多少」，不声明「这一步多久」", () => {
    expect(MOTION_THINKING.savedSecondsTypical).toBe(contract.thinking.savedSecondsTypical);
    expect(MOTION_THINKING.savedSecondsLeast).toBe(contract.thinking.savedSecondsLeast);
    expect(MOTION_THINKING.savedSecondsMost).toBe(contract.thinking.savedSecondsMost);
    expect(MOTION_THINKING.defaultEnabled).toBe(contract.thinking.defaultEnabled);
    // 绝对耗时不该出现在这份契约里——它是另一个口径的数。
    expect(contract.thinking).not.toHaveProperty("measuredSecondsWithThinking");
  });

  /**
   * 这条是上一版没有、因此没能拦住我的：文案里不许出现绝对耗时。
   *
   * 上一版写的是「编排这一步实测约 42 秒」——「编排」这个词在同一张卡片上
   * 已经被占用为整段墙钟，用户读完关掉开关，然后等了一分半到三分钟，
   * 得到的结论是「这个开关没用」或「这个界面在骗人」。
   */
  it("文案不报绝对耗时，只报差值", () => {
    for (const enabled of [true, false]) {
      const notice = motionThinkingNotice(enabled);
      expect(notice).not.toMatch(/这一步.{0,6}\d+ 秒/);
      expect(notice).toMatch(/省/);
    }
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

  it("两句文案都说清这一档与另一档差多少", () => {
    const on = motionThinkingNotice(true);
    const off = motionThinkingNotice(false);

    expect(on).toContain(`${MOTION_THINKING.savedSecondsTypical} 秒`);
    expect(off).toContain(`${MOTION_THINKING.savedSecondsTypical} 秒`);
    // 三次采样撑不起一个点估计，所以区间也要说出来。
    expect(on).toContain(`${MOTION_THINKING.savedSecondsLeast}`);
    expect(on).toContain(`${MOTION_THINKING.savedSecondsMost}`);
    // 时间之外的代价也要说：只量过时间，没量过质量。
    expect(on + off).toMatch(/质量|效果|周全|推敲/);
  });

  it("文案不写没量过的精度", () => {
    expect(motionThinkingNotice(true)).not.toMatch(/\d+\.\d/);
    expect(motionThinkingNotice(false)).not.toMatch(/\d+\.\d/);
  });
});
