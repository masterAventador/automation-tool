import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

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
});
