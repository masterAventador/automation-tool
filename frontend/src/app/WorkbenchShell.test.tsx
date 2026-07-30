import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfigProvider } from "antd";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AppUpdateGateway } from "../features/app-updates/contracts";
import type { PublishWorkspaceGateway } from "../features/publishing/publish-workspace-gateway";
import type {
  MaterialVideoStudioGateway,
  MotionRenderJobSnapshot,
} from "../features/video-studio/material-video-studio-gateway";
import {
  failMotionRun,
  motionRunSnapshot,
  resetMotionRunStore,
  settleMotionRun,
  startMotionRun,
} from "../features/video-studio/motion-run-store";
import { WorkbenchShell } from "./WorkbenchShell";

describe("workbench shell navigation", () => {
  beforeEach(() => {
    resetMotionRunStore();
  });

  it("opens video creation from the normal left navigation", async () => {
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <WorkbenchShell />
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("menuitem", { name: "创作" }));
    await user.click(screen.getByRole("radio", { name: "智能素材成片" }).closest("label")!);
    await user.click(screen.getByRole("button", { name: "打开完整制作面板" }));

    expect(screen.getByRole("heading", { name: "创作", level: 4 })).toBeVisible();
    expect(screen.getByRole("region", { name: "视频制作工作区" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "新建视频" })).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "打开完整制作界面" }),
    ).not.toBeInTheDocument();
  });

  /**
   * 视频在做的时候，人不会一直停在那一页。
   *
   * 实测：提交后切走 75 秒再回来，屏幕上没有任何东西说过「有一条正在做」——
   * 侧边栏、顶栏、导航项全都没有标记。演示当天讲解人几乎一定会切页去讲别的
   * 功能，回来看到空列表会以为没提交成功，然后再点一次，于是真的再跑一遍编排。
   *
   * 这条只管「在跑」这一半；失败那一半在下一条，因为两者说的话必须不一样。
   */
  it("marks the video entry while a film is being made", () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    startMotionRun({
      kind: "one_sentence",
      subject: "用蓝色商务风做一段本周销售增长说明",
      startedAt: Date.now(),
    });
    render(
      <QueryClientProvider client={queryClient}>
        <WorkbenchShell />
      </QueryClientProvider>,
    );

    const running = screen.getByRole("menuitem", { name: "创作" });
    expect(within(running).getByTitle("视频制作正在进行中")).toBeInTheDocument();
    // 别的导航项不该跟着亮。
    expect(
      within(screen.getByRole("menuitem", { name: "发布" })).queryByTitle(
        "视频制作正在进行中",
      ),
    ).toBeNull();
  });

  /**
   * 失败了还挂着「正在进行中」，等于把失败藏起来。
   *
   * 实测（2026-07-26 失败注入）：任务第 4 秒就失败了，用户切到别的页面，**第 12
   * 分钟界面仍然什么都不说**——全屏关键词扫描（做不出来 / 超时 / 出错 / 失败 /
   * 不可用 / 无法）全阴性。侧边栏那个点是当时唯一的标记，可它一来只有悬停才看得
   * 见的 `title`，二来那句 `title` 写的是「视频制作正在进行中」——它不但没说失败，
   * 还在说这条正在跑。这正是本项目反复吃亏的静默失败：标记在，只是它在说谎。
   *
   * 所以失败态必须自己有一句**看得见的文字**，并且不许再自称进行中。
   */
  it("says the film failed in the sidebar instead of claiming it is still running", () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    startMotionRun({
      kind: "one_sentence",
      subject: "用蓝色商务风做一段本周销售增长说明",
      startedAt: Date.now(),
    });
    render(
      <QueryClientProvider client={queryClient}>
        <WorkbenchShell />
      </QueryClientProvider>,
    );

    act(() => {
      failMotionRun({ tone: "error", text: "自动编排没能完成，视频没有开始制作。" });
    });

    // 失败那个词也进了这一项的无障碍名（视频制作 失败），所以这里按子串取——
    // 屏幕阅读器听得见它，正是这条用例要的。
    const entry = screen.getByRole("menuitem", { name: /创作/u });
    expect(within(entry).getByText("失败")).toBeVisible();
    expect(within(entry).queryByTitle("视频制作正在进行中")).toBeNull();
  });

  it("leaves the video entry unmarked when nothing is being made", () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <WorkbenchShell />
      </QueryClientProvider>,
    );

    expect(
      within(screen.getByRole("menuitem", { name: "创作" })).queryByTitle(
        "视频制作正在进行中",
      ),
    ).toBeNull();
  });

  it("opens the embedded video editing module from creation", async () => {
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <WorkbenchShell />
      </QueryClientProvider>,
    );

    expect(screen.queryByRole("menuitem", { name: "视频剪辑" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "视频制作" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("menuitem", { name: "创作" }));
    await user.click(screen.getByRole("radio", { name: "轻量剪辑" }).closest("label")!);

    expect(screen.getByRole("heading", { name: "创作", level: 4 })).toBeVisible();
    expect(screen.getByRole("region", { name: "视频剪辑工作区" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "剪辑项目" })).toBeVisible();
    expect(screen.queryByRole("region", { name: "视频制作工作区" })).not.toBeInTheDocument();
  });

  it("keeps the open source licence notice out of the main navigation", async () => {
    // The notice is a legal obligation, not a daily operating tool. It stays
    // reachable, but it no longer sits beside 视频制作 and 任务记录.
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <WorkbenchShell />
      </QueryClientProvider>,
    );

    const navigation = screen.getByRole("navigation", { name: "桌面主导航" });
    expect(
      within(navigation).queryByRole("menuitem", { name: "第三方软件声明" }),
    ).not.toBeInTheDocument();
    expect(
      within(navigation).queryByRole("menuitem", { name: "开源软件许可" }),
    ).not.toBeInTheDocument();
  });

  it("reaches the open source licence notice from the foot of settings and diagnostics", async () => {
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <WorkbenchShell />
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("menuitem", { name: "设置" }));
    const entry = screen.getByRole("button", { name: "开源软件许可" });
    expect(entry).toBeVisible();

    await user.click(entry);

    expect(screen.getByRole("heading", { name: "开源软件许可" })).toBeVisible();
    expect(screen.getByRole("region", { name: "上游开源项目" })).toBeVisible();
    expect(screen.getByRole("region", { name: "字体与素材权利" })).toBeVisible();
  });

  it("keeps 设置 marked as the section the licence notice belongs to", async () => {
    // Nothing else in the sidebar leads here, so an unselected sidebar would
    // read as a broken page rather than a sub-page of settings.
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <WorkbenchShell />
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("menuitem", { name: "设置" }));
    await user.click(screen.getByRole("button", { name: "开源软件许可" }));

    expect(screen.getByRole("menuitem", { name: "设置" })).toHaveClass(
      "ant-menu-item-selected",
    );
  });

  it("returns to settings and diagnostics through the sidebar", async () => {
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <WorkbenchShell />
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("menuitem", { name: "设置" }));
    await user.click(screen.getByRole("button", { name: "开源软件许可" }));
    await user.click(screen.getByRole("menuitem", { name: "设置" }));

    expect(screen.getByRole("heading", { name: "设置", level: 2 })).toBeVisible();
    expect(screen.queryByRole("region", { name: "上游开源项目" })).not.toBeInTheDocument();
  });

  it("keeps the upstream names off every other page in the navigation", async () => {
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <WorkbenchShell />
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("menuitem", { name: "设置" }));
    await user.click(screen.getByRole("button", { name: "开源软件许可" }));
    expect(document.body.textContent?.toLowerCase() ?? "").toContain("moneyprinterturbo");

    await user.click(screen.getByRole("menuitem", { name: "创作" }));
    const rendered = document.body.textContent?.toLowerCase() ?? "";
    for (const upstream of ["moneyprinterturbo", "hyperframes"]) {
      expect(rendered).not.toContain(upstream);
    }
  });
});

/**
 * 渲染阶段的失败必须在用户不在那一页的时候也被看见。
 *
 * T91 修掉了编排阶段（2–3 分钟）的那一半，并在自己的记录里登记了剩下的一半：
 * `refresh()` 每 2 秒轮询 `motionJobs()`，可它只在 `VideoStudio` 挂载时跑，外壳
 * 一切页就把它卸载了。于是「提交成功 → store 记的是 info 消息 → 侧边栏是蓝点
 * 『正在进行中』（此刻属实）→ 用户切走 → 渲染失败 → 没有任何东西去查」，那个蓝点
 * 就一直挂着说这条正在跑。窗口比编排短（实测一段 12 秒的成片渲染约 10 秒），但
 * 形状和 T91 修掉的那次一模一样：标记在，只是它在说谎。
 */
describe("video studio watched from anywhere in the app", () => {
  const RENDERING_JOB: MotionRenderJobSnapshot = {
    renderJobId: "b1f0d0c6-1d2f-4a0e-9c3a-2b6f5e7d8a90",
    revision: 1,
    status: "rendering",
    progressPercent: 40,
    subject: "用蓝色商务风做一段本周销售增长说明",
    styleDisplayName: "一句话自动制作",
    shotStructure: [],
    artifactId: null,
    artifactSizeBytes: null,
    failureCode: null,
  };
  const FAILED_JOB: MotionRenderJobSnapshot = {
    ...RENDERING_JOB,
    status: "failed",
    progressPercent: 62,
    failureCode: "render_failed",
  };
  const SUCCEEDED_JOB: MotionRenderJobSnapshot = {
    ...RENDERING_JOB,
    status: "succeeded",
    progressPercent: 100,
    artifactId: "2c29395b-1015-43ae-84a7-6f1901caac09",
    artifactSizeBytes: 4096,
  };
  const CANCELLED_JOB: MotionRenderJobSnapshot = {
    ...RENDERING_JOB,
    status: "cancelled",
    progressPercent: 48,
  };

  function studioGateway(
    motionJobs: MaterialVideoStudioGateway["motionJobs"],
  ): MaterialVideoStudioGateway {
    return {
      open: vi.fn().mockRejectedValue(new Error("not reached")),
      updateView: vi.fn().mockRejectedValue(new Error("not reached")),
      close: vi.fn().mockResolvedValue(undefined),
      jobs: vi.fn().mockResolvedValue([]),
      cancel: vi.fn().mockRejectedValue(new Error("not reached")),
      deleteArtifact: vi.fn().mockRejectedValue(new Error("not reached")),
      submitMotionDraft: vi.fn().mockRejectedValue(new Error("not reached")),
      motionJobs,
      cancelMotionRenderJob: vi.fn().mockRejectedValue(new Error("not reached")),
      readMotionArtifact: vi.fn().mockRejectedValue(new Error("not reached")),
      deleteMotionArtifact: vi.fn().mockRejectedValue(new Error("not reached")),
      submitMotionBrief: vi.fn().mockRejectedValue(new Error("not reached")),
      readMaterialArtifact: vi.fn().mockRejectedValue(new Error("not reached")),
    };
  }

  function openShellOnWorkbench(gateway: MaterialVideoStudioGateway) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const mounted = render(
      <QueryClientProvider client={queryClient}>
        <WorkbenchShell materialVideoStudioGateway={gateway} />
      </QueryClientProvider>,
    );
    // 用户就在别的页面上——这正是缺陷成立的前提。
    expect(screen.getByRole("heading", { name: "AI 运营助理" })).toBeVisible();
    return mounted;
  }

  /** 提交已经返回、本机渲染已经开始，而用户此刻在别的页面。 */
  function renderStartedElsewhere() {
    settleMotionRun(RENDERING_JOB.renderJobId, 12, "one_sentence", {
      tone: "info",
      text: "已提交一句话自动制作，编排完成，本机渲染开始了。",
    });
  }

  beforeEach(() => {
    resetMotionRunStore();
  });

  it("says the render failed even though the operator never came back to the page", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const motionJobs = vi.fn().mockResolvedValue([RENDERING_JOB]);
      renderStartedElsewhere();
      openShellOnWorkbench(studioGateway(motionJobs));

      motionJobs.mockResolvedValue([FAILED_JOB]);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });

      const entry = screen.getByRole("menuitem", { name: /创作/u });
      expect(within(entry).getByText("失败")).toBeVisible();
      expect(within(entry).queryByTitle("视频制作正在进行中")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  /**
   * 好消息也要送到，不能只送坏消息。
   *
   * T91b 登记的另一半：成片做好、用户在别的页面时，监视器内部已经知道任务结束了，
   * 屏幕上却和以前一模一样——蓝点，加一句 `title="视频制作正在进行中"`。对一条已经
   * 做完的片子那句话是假的，而且它把用户按在原地等一件早就发生完的事。
   */
  it("says the film is done even though the operator never came back to the page", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const motionJobs = vi.fn().mockResolvedValue([RENDERING_JOB]);
      renderStartedElsewhere();
      openShellOnWorkbench(studioGateway(motionJobs));

      motionJobs.mockResolvedValue([SUCCEEDED_JOB]);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });

      const entry = screen.getByRole("menuitem", { name: /创作/u });
      expect(within(entry).queryByTitle("视频制作正在进行中")).toBeNull();
      const mark = within(entry).getByText("完成");
      expect(mark).toBeVisible();
      /*
       * 好消息不许穿着报警的衣服。失败和未知用的是角标默认的红，用户先看到颜色才看到
       * 字；一条做好的片子也顶着红角标，会把每一次瞥向侧边栏都变成一次惊吓，然后他就
       * 不看了。这一条守的是这个决定本身——不这么钉住，以后任何人把 color 去掉都没有
       * 东西会吭声。
       */
      expect(mark.closest(".ant-badge-count")).toHaveClass("ant-badge-color-green");
    } finally {
      vi.useRealTimers();
    }
  });

  /**
   * 用户自己按的取消，之后侧边栏不该还挂着「正在进行中」。
   *
   * 这是 T101 记录第七节第 5 条登记的那一条，形状和它上面两条完全一样：监视器已经把
   * 这条任务标成 `cancelled`、定时器也停了，屏幕上却和以前一模一样——一个蓝点，悬停
   * 写着「视频制作正在进行中」。取消的严重性低于失败（用户知道自己按了什么），但它
   * 让侧边栏对着一条已经不存在的运行说话，跟前两条是同一个病。
   *
   * 断言不只是「没有蓝点」，还要「一个角标都没有」：改判据时最容易犯的错是把取消顺手
   * 归到别的分支去，于是屏幕上冒出「完成」或者「失败」——那比蓝点更糟。
   */
  it("stops marking the entry once the operator has cancelled the run", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const motionJobs = vi.fn().mockResolvedValue([RENDERING_JOB]);
      renderStartedElsewhere();
      openShellOnWorkbench(studioGateway(motionJobs));

      motionJobs.mockResolvedValue([CANCELLED_JOB]);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });

      const entry = screen.getByRole("menuitem", { name: /创作/u });
      expect(within(entry).queryByTitle("视频制作正在进行中")).toBeNull();
      expect(entry.querySelector("sup")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  /**
   * 看不见和没事发生是两件事，标记必须说得出区别。
   *
   * 把轮询搬到外壳，就等于新造了一个会自己失败的东西：桥断了、命令抛异常、原生侧
   * 没起来，`motionJobs()` 每一次都会 reject。如果那时候什么都不说，蓝点就会一直
   * 挂着「正在进行中」——本任务修的正是这个形状，不能在修它的过程中造出第二个。
   */
  it("says it cannot read the run rather than claiming it is still running", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const motionJobs = vi.fn().mockRejectedValue(new Error("bridge is down"));
      renderStartedElsewhere();
      openShellOnWorkbench(studioGateway(motionJobs));

      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });

      const entry = screen.getByRole("menuitem", { name: /创作/u });
      expect(within(entry).getByText("未知")).toBeVisible();
      expect(within(entry).queryByTitle("视频制作正在进行中")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  /** 读得到了就不要继续吓人：一次抖动不是一个持续的故障。 */
  it("goes back to the running mark once it can read the run again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const motionJobs = vi.fn().mockRejectedValue(new Error("bridge is down"));
      renderStartedElsewhere();
      openShellOnWorkbench(studioGateway(motionJobs));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });
      const entry = screen.getByRole("menuitem", { name: /创作/u });
      expect(within(entry).getByText("未知")).toBeVisible();

      motionJobs.mockResolvedValue([RENDERING_JOB]);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });

      const recovered = screen.getByRole("menuitem", { name: /创作/u });
      expect(within(recovered).queryByText("未知")).toBeNull();
      expect(within(recovered).getByTitle("视频制作正在进行中")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  /**
   * 这条守的是代价，不是行为。
   *
   * 把轮询搬到外壳最贵的一种做法，是让它在整个 App 生命周期里一直转。本会话没有
   * 提交过任何东西时，外壳一次都不该去问。
   */
  it("asks nothing at all when this session has no film outstanding", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const motionJobs = vi.fn().mockResolvedValue([]);
      openShellOnWorkbench(studioGateway(motionJobs));

      await act(async () => {
        await vi.advanceTimersByTimeAsync(60_000);
      });

      expect(motionJobs).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  /**
   * 清理漏一个，代价就是把一个过期的答案写进 store。
   *
   * 定时器好清，正在路上的那次读不好清：它可能在外壳已经拆掉之后才 settle。真要
   * 漏了，App 退出那一刻的一次读会在没人再看的时候把「失败」写进去，而下一次打开
   * App 时那条记录已经不属于任何一次运行。所以除了 `clearInterval`，还必须有一个
   * 旗子把拆卸之后回来的答案挡掉。
   */
  it("does not write an answer that arrives after it has been torn down", async () => {
    let release: (jobs: readonly MotionRenderJobSnapshot[]) => void = () => {};
    const motionJobs = vi.fn().mockReturnValue(
      new Promise<readonly MotionRenderJobSnapshot[]>((resolve) => {
        release = resolve;
      }),
    );
    renderStartedElsewhere();
    const mounted = openShellOnWorkbench(studioGateway(motionJobs));
    expect(motionJobs).toHaveBeenCalled();

    // 读还在路上时，App 关掉了。
    mounted.unmount();
    await act(async () => {
      release([FAILED_JOB]);
      await Promise.resolve();
    });

    expect(motionRunSnapshot().message?.tone).toBe("info");
    expect(motionRunSnapshot().ownJobs.get(RENDERING_JOB.renderJobId)?.outcome).toBe("running");
  });

  /** 片子做完了就不用再看着了——定时器必须自己停，不能靠用户回到那一页才停。 */
  it("stops asking once the film it was watching has ended", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const motionJobs = vi.fn().mockResolvedValue([SUCCEEDED_JOB]);
      renderStartedElsewhere();
      openShellOnWorkbench(studioGateway(motionJobs));

      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });
      const asked = motionJobs.mock.calls.length;
      expect(asked).toBeGreaterThan(0);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(60_000);
      });

      expect(motionJobs.mock.calls.length).toBe(asked);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("publishing", () => {
  function openPublishing(publishWorkspaceGateway?: PublishWorkspaceGateway) {
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <WorkbenchShell publishWorkspaceGateway={publishWorkspaceGateway} />
      </QueryClientProvider>,
    );
    return user;
  }

  it("is reachable from the main navigation", async () => {
    const user = openPublishing();

    await user.click(screen.getByRole("menuitem", { name: "发布" }));

    expect(await screen.findByRole("heading", { name: "发布", level: 2 })).toBeVisible();
  });

  it("says it cannot read the state rather than inventing a publishable one", async () => {
    // The shell has no bridge of its own; a fabricated "ready" would offer a
    // publish that nothing could carry out.
    const user = openPublishing();

    await user.click(screen.getByRole("menuitem", { name: "发布" }));
    await user.click(screen.getByRole("button", { name: /新建发布/u }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/发布状态/);
    expect(screen.queryByRole("button", { name: /发布到/ })).toBeNull();
  });

  it("lists both platforms and never says how either is reached", async () => {
    const user = openPublishing({
      async getWorkspace() {
        return {
          platforms: [
            { platform: "bilibili", availability: "awaiting_configuration" },
            { platform: "douyin", availability: "ready" },
          ],
          stage: "idle",
          target: null,
          approval: null,
          outcome: null,
          retryable: false,
          audit: [],
        };
      },
      async beginPublish() {
        throw new Error("not reached");
      },
      async approvePublish() {
        throw new Error("not reached");
      },
      async cancelPublish() {
        throw new Error("not reached");
      },
    });

    await user.click(screen.getByRole("menuitem", { name: "发布" }));
    await user.click(screen.getByRole("button", { name: /新建发布/u }));
    await screen.findByText("真实发布工作台");

    expect(screen.getByText("B站")).toBeVisible();
    expect(screen.getByText("抖音")).toBeVisible();
    const rendered = document.body.textContent?.toLowerCase() ?? "";
    for (const upstream of ["browser use", "playwright", "chromium", "browser_use"]) {
      expect(rendered).not.toContain(upstream);
    }
  });
});

describe("finished video handed to the publishing page", () => {
  const readyWorkspace: PublishWorkspaceGateway = {
    async getWorkspace() {
      return {
        platforms: [
          { platform: "bilibili", availability: "awaiting_configuration" },
          { platform: "douyin", availability: "ready" },
        ],
        stage: "idle",
        target: null,
        approval: null,
        outcome: null,
        retryable: false,
        audit: [],
      };
    },
    async beginPublish() {
      throw new Error("not reached");
    },
    async approvePublish() {
      throw new Error("not reached");
    },
    async cancelPublish() {
      throw new Error("not reached");
    },
  };

  const chosen = {
    artifactId: "423e4567-e89b-42d3-a456-426614174001",
    videoSummary: "护肤知识讲解 · 12.4 MB",
  } as const;

  function openShell(selectedVideo?: typeof chosen) {
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <WorkbenchShell
          publishWorkspaceGateway={readyWorkspace}
          {...(selectedVideo === undefined ? {} : { selectedVideo })}
        />
      </QueryClientProvider>,
    );
    return user;
  }

  it("carries the chosen video into the publishing page", async () => {
    const user = openShell(chosen);

    await user.click(screen.getByRole("menuitem", { name: "发布" }));
    await user.click(screen.getByRole("button", { name: /新建发布/u }));

    const pending = await screen.findByRole("group", { name: "待发布视频" });
    expect(within(pending).getByText("护肤知识讲解 · 12.4 MB")).toBeVisible();
  });

  it("sends the operator back to the finished videos to swap the selection", async () => {
    // "换一个" is the only way back. Leaving the old selection in place while
    // the operator goes looking for another one is how the wrong video gets
    // published.
    const user = openShell(chosen);
    await user.click(screen.getByRole("menuitem", { name: "发布" }));
    await user.click(screen.getByRole("button", { name: /新建发布/u }));
    const pending = await screen.findByRole("group", { name: "待发布视频" });

    await user.click(within(pending).getByRole("button", { name: "换一个" }));

    expect(screen.getByRole("heading", { name: "创作", level: 2 })).toBeVisible();
    await user.click(screen.getByRole("menuitem", { name: "发布" }));
    await user.click(screen.getByRole("button", { name: /新建发布/u }));
    await screen.findByText("真实发布工作台");
    expect(screen.queryByRole("group", { name: "待发布视频" })).toBeNull();
  });

  it("offers nothing to publish until a video has been chosen", async () => {
    const user = openShell();

    await user.click(screen.getByRole("menuitem", { name: "发布" }));
    await user.click(screen.getByRole("button", { name: /新建发布/u }));
    await screen.findByText("真实发布工作台");

    expect(screen.queryByRole("group", { name: "待发布视频" })).toBeNull();
    expect(screen.queryByRole("button", { name: /发布到/ })).toBeNull();
  });
});

/**
 * 强制更新只有打开「设置」才看得见——AI-first 改版留下的一个真缺陷。
 *
 * `AppUpdateCenter` 的设计是：提示用的 Modal **无条件**渲染，`showSettings`
 * 只额外加一张管理卡。也就是说它挂在哪里，提示就只能在哪里弹。改版把它挪进了
 * 设置页那一支 `showingSettings ? … : …`，于是提示的可见性被绑在了「用户此刻
 * 正好在设置页」上。
 *
 * 可选更新如此已经不好，**强制更新**如此是真问题：用户可以永远不打开设置，
 * 而那条更新的语义正是「不更新就不能继续用」。
 *
 * 由 H8-21 的桌面验收发现（它开机后直接等「发现新版本」，等了 25 秒没等到）。
 * 这条把复现压到组件层，跑得起来也定位得准。
 */
/**
 * Ant Design's Modal leaves a hidden pre-render behind, so the first node
 * carrying the title is not the one on screen — `AppUpdateCenter.test.tsx`
 * already learnt this and takes the last match while retrying past the
 * animation. Same shape here rather than a second way of asking.
 */
async function expectVisibleHeading(name: string): Promise<void> {
  await waitFor(() => {
    expect(screen.getAllByRole("heading", { name }).at(-1)).toBeVisible();
  });
}

describe("app update prompt visibility", () => {
  function promptingGateway(action: "prompt" | "forced"): AppUpdateGateway {
    const release = {
      version: "0.2.0",
      channel: "stable",
      policy: action === "forced" ? "forced" : "optional",
      notes: "",
      publishedAt: "2026-07-29T00:00:00Z",
      artifact: {
        target: "darwin",
        arch: "aarch64",
        sha256: "b".repeat(64),
        sizeBytes: 2048,
      },
    } as const;
    return {
      getState: vi.fn().mockResolvedValue({ state: "ready", release, action }),
      checkNow: vi.fn().mockResolvedValue({ state: "ready", release, action }),
      decide: vi.fn().mockResolvedValue({ state: "installation_launched", release }),
    };
  }

  it("offers an optional update without the user opening 设置", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <ConfigProvider theme={{ token: { motion: false } }}>
        <QueryClientProvider client={queryClient}>
          <WorkbenchShell appUpdateGateway={promptingGateway("prompt")} />
        </QueryClientProvider>
      </ConfigProvider>,
    );

    await expectVisibleHeading("发现新版本 0.2.0");
  });

  it("shows a forced update without the user opening 设置", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <ConfigProvider theme={{ token: { motion: false } }}>
        <QueryClientProvider client={queryClient}>
          <WorkbenchShell appUpdateGateway={promptingGateway("forced")} />
        </QueryClientProvider>
      </ConfigProvider>,
    );

    await expectVisibleHeading("必须更新到 0.2.0");
  });
});

/**
 * 打开过一条任务之后，「查看运行记录」就再也回不到列表了。
 *
 * `selectedTaskId` 全文件只有一处写入（`openTask`），**从来没有被清空过**。
 * 而运行记录页的渲染是：
 *
 *   showingTaskRun && selectedTaskId !== null  →  任务运行详情
 *   showingTaskRun                             →  运行记录列表
 *
 * 于是用户只要点开过任何一条任务，这一页在本次会话里就永远停在那一条上，
 * 唯一的出路是重启 App。而「返回工作台」只做了 setActivePage("automation")，
 * 没有清掉选中。
 *
 * 由 T3-18 的桌面验收发现：它取消完第一条任务、点「返回工作台」、再进运行记录，
 * 然后要打开第二条——而页面还停在第一条的详情上，列表里一行都没有。
 * 那条 spec 为此红了三轮，因为「没等到某一行」这句话说不出「页面根本不是列表」。
 */
function taskSourceWithOneTask() {
  const task = {
    taskId: "6f2f0a4e-9a6f-4c1e-9a0a-1f5c0f9d3b21",
    status: "succeeded",
    revision: 3,
    lastEventSequence: 4,
    createdAt: "2026-07-29T05:00:00Z",
    updatedAt: "2026-07-29T05:00:30Z",
  } as const;
  return {
    getTask: vi.fn(async () => task),
    listTasks: vi.fn(async () => ({ items: [task], nextCursor: null })),
    // 只在 abort 时 resolve。初版立即 resolve，消费方于是不停重订阅，
    // 直接把 node 跑成 heap out of memory——这条假的必须和真实现同语义。
    streamTaskEvents: vi.fn(
      async (
        _taskId: string,
        afterSequence: number,
        _onEvent: unknown,
        options: { signal?: AbortSignal } = {},
      ) =>
        new Promise((resolve) => {
          options.signal?.addEventListener(
            "abort",
            () => resolve({ lastSequence: afterSequence, terminal: false }),
            { once: true },
          );
        }),
    ),
  } as unknown as Parameters<typeof WorkbenchShell>[0]["taskSource"];
}

describe("run records reachability", () => {
  it("returns to the list after leaving a task, not to the task just left", async () => {
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <WorkbenchShell taskSource={taskSourceWithOneTask()} />
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("menuitem", { name: "自动化" }));
    await user.click(screen.getByRole("button", { name: "查看运行记录" }));
    expect(await screen.findByRole("heading", { name: "运行记录", level: 2 })).toBeVisible();

    // 打开任意一条任务，再返回。
    //
    // 这里**不能**写「没有任务就 return」——初版就是那么写的，于是在没有任务数据的
    // 默认外壳上直接绿了，而缺陷原封不动。那是一条不可能失败的用例，比没有更糟。
    const rows = await screen.findAllByRole("button", { name: /的任务$/ });
    await user.click(rows[0]!);
    await user.click(await screen.findByRole("button", { name: "返回工作台" }));

    // 再次进入运行记录：应当是列表，而不是刚才那条任务的详情。
    await user.click(screen.getByRole("button", { name: "查看运行记录" }));
    expect(await screen.findByRole("heading", { name: "运行记录", level: 2 })).toBeVisible();
  });
});
