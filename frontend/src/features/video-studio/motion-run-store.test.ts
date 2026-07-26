import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  dismissMotionRunMessage,
  endMotionJob,
  failMotionRun,
  forgetMotionJob,
  motionRunAttention,
  motionRunNeedsWatch,
  motionRunSnapshot,
  reportMotionRunTracking,
  resetMotionRunStore,
  setMotionActiveTab,
  setMotionBrief,
  setMotionMethod,
  settleMotionRun,
  startMotionRun,
  subscribeMotionRun,
} from "./motion-run-store";

const JOB_ID = "f89d8f18-6b4e-4f5a-8325-8da45f71d7e2";

const PENDING = {
  kind: "one_sentence",
  subject: "用蓝色商务风做一段本周销售增长说明",
  startedAt: 1_700_000_000_000,
} as const;

describe("motion run store", () => {
  beforeEach(() => {
    resetMotionRunStore();
  });

  /**
   * 提交那一刻就要有记录，不能等编排成功才有。
   *
   * 原生侧 `submit_motion_video_brief` 是编排跑完才写任务快照的：实测那次成功
   * 的运行，任务是在 +140 秒才第一次出现。也就是说前 136 秒即使用户不切页，
   * 「制作任务」页也是空的——他点完提交看到一个空列表，会以为没提交上去，
   * 然后再点一次。
   */
  it("records a submission from the moment it is sent, not from when it succeeds", () => {
    startMotionRun(PENDING);

    expect(motionRunSnapshot().pending).toEqual(PENDING);
    expect(motionRunAttention(motionRunSnapshot())).toBe("running");
  });

  /**
   * 失败必须活过页面卸载。
   *
   * 这是最危险的一条：编排的 Promise 是几分钟后才 settle 的，如果那时组件已经
   * 因为切页被卸载，错误文案就写进了死掉的 state，用户永远不知道任务已经挂了。
   * 云端验收线的 run2 就是这么把失败原因永久弄丢的。
   */
  it("keeps a failure after the page that started it is gone", () => {
    startMotionRun(PENDING);
    failMotionRun({ tone: "error", text: "自动编排中途出错，视频没有开始制作。" });

    expect(motionRunSnapshot().pending).toBeNull();
    expect(motionRunSnapshot().message).toEqual({
      tone: "error",
      text: "自动编排中途出错，视频没有开始制作。",
    });
    expect(motionRunAttention(motionRunSnapshot())).toBe("failed");

    dismissMotionRunMessage();
    expect(motionRunSnapshot().message).toBeNull();
    expect(motionRunAttention(motionRunSnapshot())).toBe("none");
  });

  it("hands the pending row over to the real job once the run returns", () => {
    startMotionRun(PENDING);
    settleMotionRun(JOB_ID, 12, {
      tone: "info",
      text: "已提交一句话自动制作。",
    });

    expect(motionRunSnapshot().pending).toBeNull();
    expect(motionRunSnapshot().ownJobs.get(JOB_ID)).toEqual({
      startedAt: expect.any(Number),
      filmSeconds: 12,
      outcome: "running",
    });

    forgetMotionJob(JOB_ID);
    expect(motionRunSnapshot().ownJobs.size).toBe(0);
  });

  /**
   * 提交返回之后，本机渲染那一段仍然要有人看着。
   *
   * T91 把结果搬出了组件，可**去查结果的那个动作**还留在组件里：`refresh()` 只在
   * `VideoStudio` 挂载时轮询。于是渲染阶段失败且用户不在那一页时没有任何东西去查。
   * 「还要不要看着」必须是一个能从 store 本身算出来的事实，外壳才能据此决定要不要
   * 起那个定时器——也才能在没事的时候一次都不问。
   */
  it("knows a film still needs watching from the moment the render starts", () => {
    expect(motionRunNeedsWatch(motionRunSnapshot())).toBe(false);

    settleMotionRun(JOB_ID, 12, { tone: "info", text: "本机渲染开始了。" });
    expect(motionRunNeedsWatch(motionRunSnapshot())).toBe(true);

    endMotionJob(JOB_ID, "succeeded");
    expect(motionRunNeedsWatch(motionRunSnapshot())).toBe(false);
    // 结束不等于忘掉：成片还要在页面上被announce一次，任务卡也还在。
    expect(motionRunSnapshot().ownJobs.has(JOB_ID)).toBe(true);
  });

  /**
   * 做好了也要送到，不只是坏消息才送。
   *
   * T91b 把「渲染失败且用户不在这一页」修掉了，并在自己的记录里登记了同族的另一半：
   * 成片做好、用户在别的页面时，监视器内部已经把这条任务标成结束了，可屏幕上和以前
   * 一模一样——侧边栏仍是蓝点，`title` 仍写着「正在进行中」。对一条已经做完的片子，
   * 那句话是假的。严重性比失败那一半低一档（这是好消息没送到，不是坏消息被伪装成
   * 好消息），但形状同族，而且演示当天客户切走再切回来看到「还在做」一样难看。
   */
  it("says the film is finished rather than still claiming it is being made", () => {
    settleMotionRun(JOB_ID, 12, { tone: "info", text: "本机渲染开始了。" });
    expect(motionRunAttention(motionRunSnapshot())).toBe("running");

    endMotionJob(JOB_ID, "succeeded");

    expect(motionRunAttention(motionRunSnapshot())).toBe("finished");
  });

  /**
   * 看过了就不该再提醒，而「看过了」只有一个判据。
   *
   * 那个判据是页面上的「去看成片」——它调 `forgetMotionJob`，同一个动作把页面上的
   * 成功提示和侧边栏的标记一起灭掉。这里没有第二条规则：不看计时、不看用户有没有
   * 路过那一页、也不因为又提交了一条而清掉。多一条规则就多一个两边说法不一致的
   * 机会，而两个界面对同一件事说不同的话正是本条线一直在修的病。
   */
  it("stops offering a film once the operator has opened it", () => {
    settleMotionRun(JOB_ID, 12, { tone: "info", text: "本机渲染开始了。" });
    endMotionJob(JOB_ID, "succeeded");
    expect(motionRunAttention(motionRunSnapshot())).toBe("finished");

    // 页面上那个按钮做的正是这两件事。
    forgetMotionJob(JOB_ID);
    dismissMotionRunMessage();

    expect(motionRunAttention(motionRunSnapshot())).toBe("none");
  });

  /**
   * 用户自己按的取消，不是一条等他去看的成片。
   *
   * 取消和做好都让任务到达终态、都让监视器停下来，可只有后者欠着用户一次「去看」。
   * 把两者压成同一个「已结束」标志，侧边栏就会对着一条用户亲手掐掉的任务说「完成」。
   */
  it("does not offer a film for a render the operator cancelled himself", () => {
    settleMotionRun(JOB_ID, 12, { tone: "info", text: "本机渲染开始了。" });

    endMotionJob(JOB_ID, "cancelled");

    expect(motionRunNeedsWatch(motionRunSnapshot())).toBe(false);
    expect(motionRunAttention(motionRunSnapshot())).not.toBe("finished");
  });

  /**
   * 一个角标只有一格，两件事同时成立时它必须拿出更要紧的那一件。
   *
   * 排序是：已知的坏结果 → 看不见 → 有东西可以取 → 有东西在跑。坏消息和看不见都是
   * 问题，做好了不是问题但可以动手，正在跑则什么都不用做。
   */
  it("keeps a failure and a blind spot ahead of a film waiting to be opened", () => {
    const OTHER_JOB = "0f2f0e56-2f1d-4a2b-8f3c-1d4e5f6a7b8c";
    settleMotionRun(JOB_ID, 12, { tone: "info", text: "本机渲染开始了。" });
    endMotionJob(JOB_ID, "succeeded");
    // 第二条还在跑，而且现在读不到它。
    settleMotionRun(OTHER_JOB, 12, { tone: "info", text: "本机渲染开始了。" });
    reportMotionRunTracking("lost");
    expect(motionRunAttention(motionRunSnapshot())).toBe("unknown");

    failMotionRun({ tone: "error", text: "这条视频没有做出来。" });
    expect(motionRunAttention(motionRunSnapshot())).toBe("failed");
  });

  it("ignores an ending for a job it never started", () => {
    settleMotionRun(JOB_ID, 12, { tone: "info", text: "本机渲染开始了。" });
    const before = motionRunSnapshot();

    endMotionJob("00000000-0000-4000-8000-000000000000", "succeeded");

    expect(motionRunSnapshot()).toBe(before);
  });

  /**
   * 看不见和没事发生必须说得出区别。
   *
   * 一旦把轮询搬出组件，就多了一个会自己失败的东西。它失败的时候如果什么都不说，
   * 标记就会停在「正在进行中」——那正是本任务在修的形状，不能自己再造一个。
   */
  it("says the run cannot be read rather than leaving it looking healthy", () => {
    settleMotionRun(JOB_ID, 12, { tone: "info", text: "本机渲染开始了。" });
    expect(motionRunAttention(motionRunSnapshot())).toBe("running");

    reportMotionRunTracking("lost");
    expect(motionRunAttention(motionRunSnapshot())).toBe("unknown");

    reportMotionRunTracking("ok");
    expect(motionRunAttention(motionRunSnapshot())).toBe("running");
  });

  it("keeps a real failure ahead of merely not being able to look", () => {
    settleMotionRun(JOB_ID, 12, { tone: "info", text: "本机渲染开始了。" });
    reportMotionRunTracking("lost");
    failMotionRun({ tone: "error", text: "这条视频没有做出来。" });

    expect(motionRunAttention(motionRunSnapshot())).toBe("failed");
  });

  it("does not raise a mark for a lost view of nothing", () => {
    reportMotionRunTracking("lost");

    expect(motionRunNeedsWatch(motionRunSnapshot())).toBe(false);
    expect(motionRunAttention(motionRunSnapshot())).toBe("none");
  });

  it("holds the sentence, the chosen method and the open tab across a page change", () => {
    setMotionBrief("用蓝色商务风做一段说明");
    setMotionMethod("motion_composition_v1");
    setMotionActiveTab("jobs");

    expect(motionRunSnapshot().brief).toBe("用蓝色商务风做一段说明");
    expect(motionRunSnapshot().selectedMethod).toBe("motion_composition_v1");
    expect(motionRunSnapshot().activeTab).toBe("jobs");
    // 只是把句子放在这里，本身不构成「有东西要看」。
    expect(motionRunAttention(motionRunSnapshot())).toBe("none");
  });

  it("tells subscribers on every change and stops the moment one leaves", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeMotionRun(listener);

    startMotionRun(PENDING);
    expect(listener).toHaveBeenCalledTimes(1);

    failMotionRun({ tone: "error", text: "失败了。" });
    expect(listener).toHaveBeenCalledTimes(2);

    unsubscribe();
    setMotionBrief("再写一句");
    expect(listener).toHaveBeenCalledTimes(2);
  });

  // 快照必须是同一个对象引用，否则 useSyncExternalStore 会判定「每次都变了」
  // 而无限重渲染。
  it("returns a stable snapshot while nothing has changed", () => {
    expect(motionRunSnapshot()).toBe(motionRunSnapshot());

    const before = motionRunSnapshot();
    setMotionBrief("变了");
    expect(motionRunSnapshot()).not.toBe(before);
  });
});
