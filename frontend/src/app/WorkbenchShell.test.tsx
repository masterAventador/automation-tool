import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it } from "vitest";

import type { PublishWorkspaceGateway } from "../features/publishing/publish-workspace-gateway";
import {
  failMotionRun,
  resetMotionRunStore,
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

    await user.click(screen.getByRole("menuitem", { name: "视频制作" }));

    expect(screen.getByRole("heading", { name: "视频制作" })).toBeVisible();
    expect(screen.getByRole("region", { name: "视频制作工作区" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "新建视频" })).toBeVisible();
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

    const running = screen.getByRole("menuitem", { name: "视频制作" });
    expect(within(running).getByTitle("视频制作正在进行中")).toBeInTheDocument();
    // 别的导航项不该跟着亮。
    expect(
      within(screen.getByRole("menuitem", { name: "视频剪辑" })).queryByTitle(
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
    const entry = screen.getByRole("menuitem", { name: /视频制作/u });
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
      within(screen.getByRole("menuitem", { name: "视频制作" })).queryByTitle(
        "视频制作正在进行中",
      ),
    ).toBeNull();
  });

  it("opens the standalone video editing module from its own left entry", async () => {
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <WorkbenchShell />
      </QueryClientProvider>,
    );

    expect(screen.getByRole("menuitem", { name: "视频剪辑" })).toBeVisible();
    expect(screen.getByRole("menuitem", { name: "视频制作" })).toBeVisible();
    await user.click(screen.getByRole("menuitem", { name: "视频剪辑" }));

    expect(screen.getByRole("heading", { name: "视频剪辑" })).toBeVisible();
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

    await user.click(screen.getByRole("menuitem", { name: "设置与诊断" }));
    const entry = screen.getByRole("button", { name: "开源软件许可" });
    expect(entry).toBeVisible();

    await user.click(entry);

    expect(screen.getByRole("heading", { name: "开源软件许可" })).toBeVisible();
    expect(screen.getByRole("region", { name: "上游开源项目" })).toBeVisible();
    expect(screen.getByRole("region", { name: "字体与素材权利" })).toBeVisible();
  });

  it("keeps 设置与诊断 marked as the section the licence notice belongs to", async () => {
    // Nothing else in the sidebar leads here, so an unselected sidebar would
    // read as a broken page rather than a sub-page of settings.
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <WorkbenchShell />
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("menuitem", { name: "设置与诊断" }));
    await user.click(screen.getByRole("button", { name: "开源软件许可" }));

    expect(screen.getByRole("menuitem", { name: "设置与诊断" })).toHaveClass(
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

    await user.click(screen.getByRole("menuitem", { name: "设置与诊断" }));
    await user.click(screen.getByRole("button", { name: "开源软件许可" }));
    await user.click(screen.getByRole("menuitem", { name: "设置与诊断" }));

    expect(screen.getByRole("heading", { name: "设置与诊断" })).toBeVisible();
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

    await user.click(screen.getByRole("menuitem", { name: "设置与诊断" }));
    await user.click(screen.getByRole("button", { name: "开源软件许可" }));
    expect(document.body.textContent?.toLowerCase() ?? "").toContain("moneyprinterturbo");

    await user.click(screen.getByRole("menuitem", { name: "视频制作" }));
    const rendered = document.body.textContent?.toLowerCase() ?? "";
    for (const upstream of ["moneyprinterturbo", "hyperframes"]) {
      expect(rendered).not.toContain(upstream);
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

    await user.click(screen.getByRole("menuitem", { name: "作品发布" }));

    expect(await screen.findByRole("heading", { name: "作品发布" })).toBeVisible();
  });

  it("says it cannot read the state rather than inventing a publishable one", async () => {
    // The shell has no bridge of its own; a fabricated "ready" would offer a
    // publish that nothing could carry out.
    const user = openPublishing();

    await user.click(screen.getByRole("menuitem", { name: "作品发布" }));

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

    await user.click(screen.getByRole("menuitem", { name: "作品发布" }));
    await screen.findByRole("heading", { name: "作品发布" });

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

    await user.click(screen.getByRole("menuitem", { name: "作品发布" }));

    const pending = await screen.findByRole("group", { name: "待发布视频" });
    expect(within(pending).getByText("护肤知识讲解 · 12.4 MB")).toBeVisible();
  });

  it("sends the operator back to the finished videos to swap the selection", async () => {
    // "换一个" is the only way back. Leaving the old selection in place while
    // the operator goes looking for another one is how the wrong video gets
    // published.
    const user = openShell(chosen);
    await user.click(screen.getByRole("menuitem", { name: "作品发布" }));
    const pending = await screen.findByRole("group", { name: "待发布视频" });

    await user.click(within(pending).getByRole("button", { name: "换一个" }));

    expect(screen.getByRole("region", { name: "视频制作工作区" })).toBeVisible();
    await user.click(screen.getByRole("menuitem", { name: "作品发布" }));
    await screen.findByRole("heading", { name: "作品发布" });
    expect(screen.queryByRole("group", { name: "待发布视频" })).toBeNull();
  });

  it("offers nothing to publish until a video has been chosen", async () => {
    const user = openShell();

    await user.click(screen.getByRole("menuitem", { name: "作品发布" }));
    await screen.findByRole("heading", { name: "作品发布" });

    expect(screen.queryByRole("group", { name: "待发布视频" })).toBeNull();
    expect(screen.queryByRole("button", { name: /发布到/ })).toBeNull();
  });
});
