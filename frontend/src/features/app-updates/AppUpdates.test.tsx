import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type {
  AppUpdateDecision,
  AppUpdateGateway,
  AppUpdateState,
} from "./contracts";
import { AppUpdateSettings, AppUpdates } from "./AppUpdates";

const release = {
  version: "1.2.3",
  channel: "stable",
  policy: "optional",
  notes: "稳定性与安全更新",
  publishedAt: "2026-07-22T00:00:00Z",
  artifact: {
    target: "darwin",
    arch: "aarch64",
    sha256: "a".repeat(64),
    sizeBytes: 4096,
  },
} as const;

function gateway(initial: AppUpdateState): AppUpdateGateway {
  return {
    getState: vi.fn().mockResolvedValue(initial),
    checkNow: vi.fn().mockResolvedValue({ state: "up_to_date", trigger: "manual" }),
    decide: vi.fn(
      async (decision: AppUpdateDecision): Promise<AppUpdateState> => ({
        state: "ready",
        release,
        action:
          decision === "defer"
            ? "deferred"
            : decision === "skip_version"
              ? "skipped"
              : "install_requested",
      }),
    ),
  };
}

describe("generic App updates UI", () => {
  it("offers install, defer and skip only for an optional prompt", async () => {
    const updates = gateway({ state: "ready", release, action: "prompt" });
    const user = userEvent.setup();
    render(
      <AppUpdates gateway={updates} pollIntervalMs={60_000}>
        <AppUpdateSettings />
      </AppUpdates>,
    );

    const dialog = await screen.findByRole("dialog", { name: "发现新版本 1.2.3" });
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByText("稳定性与安全更新")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "立即安装" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "暂不安装" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "跳过此版本" })).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "暂不安装" }));

    expect(updates.decide).toHaveBeenCalledWith("defer");
    expect(await screen.findByText("版本 1.2.3 已暂缓安装")).toBeVisible();
  });

  it("routes the settings entry through the same manual native check", async () => {
    const updates = gateway({ state: "idle" });
    const user = userEvent.setup();
    render(
      <AppUpdates gateway={updates} pollIntervalMs={60_000}>
        <AppUpdateSettings />
      </AppUpdates>,
    );

    await user.click(await screen.findByRole("button", { name: "检查更新" }));

    expect(updates.checkNow).toHaveBeenCalledOnce();
    expect(await screen.findByText("当前已是最新版本")).toBeVisible();
  });

  it("shows forced readiness without optional decisions", async () => {
    const updates = gateway({
      state: "ready",
      release: { ...release, policy: "forced" },
      action: "forced",
    });
    render(
      <AppUpdates gateway={updates} pollIntervalMs={60_000}>
        <AppUpdateSettings />
      </AppUpdates>,
    );

    expect(await screen.findByText("必须安装的更新已准备好")).toBeVisible();
    expect(screen.getByText("将在下次启动 App 时自动安装。")).toBeVisible();
    expect(screen.queryByRole("button", { name: "暂不安装" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "跳过此版本" })).not.toBeInTheDocument();
  });

  it("renders only fixed copy when the native boundary fails", async () => {
    const updates = gateway({ state: "idle" });
    vi.mocked(updates.getState).mockRejectedValue(new Error("token=private-update-secret"));
    render(
      <AppUpdates gateway={updates} pollIntervalMs={60_000}>
        <AppUpdateSettings />
      </AppUpdates>,
    );

    expect(await screen.findByText("暂时无法读取更新状态。请稍后重试。")).toBeVisible();
    expect(document.body).not.toHaveTextContent("private-update-secret");
  });
});
