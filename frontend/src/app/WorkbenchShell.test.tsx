import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import type { PublishWorkspaceGateway } from "../features/publishing/publish-workspace-gateway";
import { WorkbenchShell } from "./WorkbenchShell";

describe("workbench shell navigation", () => {
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
