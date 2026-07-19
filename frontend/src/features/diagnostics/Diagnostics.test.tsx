import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { PlatformAdapter } from "../../platform/types";
import { Diagnostics } from "./Diagnostics";

function platformAdapter(): PlatformAdapter {
  return {
    getBrowserSettings: vi.fn().mockResolvedValue({
      availableBrowsers: [],
      selectedBrowser: null,
    }),
    selectBrowser: vi.fn(),
    getExecutorStatus: vi.fn().mockResolvedValue({
      state: "stopped",
      version: null,
      buildId: null,
      restartCount: 0,
    }),
    restartExecutor: vi.fn().mockResolvedValue({
      state: "running",
      version: "0.1.0",
      buildId: "demo-build",
      restartCount: 0,
    }),
    getExecutorDiagnostics: vi.fn().mockResolvedValue(["safe diagnostic"]),
    emergencyStopExecutor: vi.fn().mockResolvedValue({
      state: "stopped",
      version: null,
      buildId: null,
      restartCount: 0,
    }),
  };
}

describe("Executor diagnostics", () => {
  it("loads status and diagnostics and restarts through PlatformAdapter", async () => {
    const adapter = platformAdapter();
    const user = userEvent.setup();

    render(<Diagnostics platform={adapter} />);

    expect(await screen.findByText("本地执行器已停止")).toBeVisible();
    expect(screen.getByText("safe diagnostic")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "启动执行器" }));
    expect(await screen.findByText("本地执行器运行中")).toBeVisible();
    expect(adapter.restartExecutor).toHaveBeenCalledTimes(1);
  });

  it("requires confirmation before the local process-tree emergency stop", async () => {
    const adapter = platformAdapter();
    const user = userEvent.setup();

    render(<Diagnostics platform={adapter} />);

    expect(await screen.findByText("本地执行器已停止")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "本地紧急停止" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("仅停止本机执行器进程树")).toBeInTheDocument();
    expect(adapter.emergencyStopExecutor).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "确认停止" }));
    expect(adapter.emergencyStopExecutor).toHaveBeenCalledTimes(1);
  });

  it("refreshes the native status without reloading the desktop window", async () => {
    const adapter = platformAdapter();
    vi.mocked(adapter.getExecutorStatus)
      .mockResolvedValueOnce({
        state: "stopped",
        version: null,
        buildId: null,
        restartCount: 0,
      })
      .mockResolvedValueOnce({
        state: "running",
        version: "0.1.0",
        buildId: "recovered-build",
        restartCount: 1,
      });
    const user = userEvent.setup();

    render(<Diagnostics platform={adapter} />);

    expect(await screen.findByText("本地执行器已停止")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "刷新状态" }));
    expect(await screen.findByText("本地执行器运行中")).toBeVisible();
    expect(screen.getByText("recovered-build")).toBeVisible();
    expect(adapter.getExecutorStatus).toHaveBeenCalledTimes(2);
  });

  it("shows only a fixed safe message when native calls fail", async () => {
    const adapter = platformAdapter();
    vi.mocked(adapter.getExecutorStatus).mockRejectedValue(
      new Error("session_token=private-native-secret"),
    );

    render(<Diagnostics platform={adapter} />);

    expect(await screen.findByText("暂时无法读取本地执行器状态。请稍后重试。")).toBeVisible();
    expect(document.body).not.toHaveTextContent("private-native-secret");
  });
});
