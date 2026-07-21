import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { PlatformAdapter } from "../../platform/types";
import { BrowserSettings } from "./BrowserSettings";

function platformAdapter(): PlatformAdapter {
  return {
    getBrowserSettings: vi.fn().mockResolvedValue({
      availableBrowsers: ["google_chrome", "microsoft_edge"],
      selectedBrowser: "google_chrome",
    }),
    selectBrowser: vi.fn().mockResolvedValue({
      availableBrowsers: ["google_chrome", "microsoft_edge"],
      selectedBrowser: "microsoft_edge",
    }),
    getExecutorStatus: vi.fn().mockResolvedValue({
      state: "stopped",
      version: null,
      buildId: null,
      restartCount: 0,
    }),
    restartExecutor: vi.fn(),
    getExecutorDiagnostics: vi.fn().mockResolvedValue([]),
    exportDiagnostics: vi.fn(),
    emergencyStopExecutor: vi.fn(),
    getBrowserDiagnosticSettings: vi.fn().mockResolvedValue({ captureSuccessfulRuns: false }),
    setCaptureSuccessfulDiagnostics: vi.fn(),
  };
}

describe("Browser settings", () => {
  it("selects only an available browser enum through PlatformAdapter", async () => {
    const platform = platformAdapter();
    const user = userEvent.setup();
    render(<BrowserSettings platform={platform} />);

    expect(await screen.findByText("当前选择：Google Chrome")).toBeVisible();
    await user.click(screen.getByRole("radio", { name: "Microsoft Edge" }));
    await user.click(screen.getByRole("button", { name: "保存浏览器选择" }));

    expect(platform.selectBrowser).toHaveBeenCalledWith("microsoft_edge");
    expect(await screen.findByText("当前选择：Microsoft Edge")).toBeVisible();
  });

  it("shows a fixed empty state when no trusted browser is installed", async () => {
    const platform = platformAdapter();
    vi.mocked(platform.getBrowserSettings).mockResolvedValue({
      availableBrowsers: [],
      selectedBrowser: null,
    });
    render(<BrowserSettings platform={platform} />);

    expect(await screen.findByText("未发现受支持的浏览器")).toBeVisible();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("never reflects native browser errors", async () => {
    const platform = platformAdapter();
    vi.mocked(platform.getBrowserSettings).mockRejectedValue(
      new Error("/private/path/secret-browser"),
    );
    render(<BrowserSettings platform={platform} />);

    expect(await screen.findByText("暂时无法读取浏览器设置。请稍后重试。")).toBeVisible();
    expect(document.body).not.toHaveTextContent("secret-browser");
  });
});
