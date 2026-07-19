import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { PlatformSessionGateway } from "./platform-session-gateway";
import { PlatformSessions } from "./PlatformSessions";

function gateway(): PlatformSessionGateway {
  return {
    getDouyinSession: vi.fn().mockResolvedValue({
      platform: "douyin",
      state: "missing",
      observedAt: "2026-07-19T14:30:00Z",
    }),
    openDouyinLogin: vi.fn().mockResolvedValue({
      platform: "douyin",
      state: "awaiting_scan",
      flowVersion: "douyin.qr-login.v2",
    }),
    recheckDouyinLogin: vi.fn().mockResolvedValue({
      platform: "douyin",
      state: "healthy",
      flowVersion: "douyin.qr-login.v2",
    }),
  };
}

describe("platform status page", () => {
  it("shows server health and drives the original local handling entry", async () => {
    const source = gateway();
    const user = userEvent.setup();
    render(<PlatformSessions gateway={source} />);

    expect(await screen.findByText("需要登录")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "打开登录处理" }));
    expect(source.openDouyinLogin).toHaveBeenCalledOnce();
    expect(await screen.findByText("请在打开的运营浏览器中扫码登录。" )).toBeVisible();

    await user.click(screen.getByRole("button", { name: "我已处理，重新检查" }));
    expect(source.recheckDouyinLogin).toHaveBeenCalledOnce();
    expect(await screen.findByText("登录正常")).toBeVisible();
    expect(screen.getByText("需要登录")).toBeVisible();
  });

  it("exposes but does not fake the B5-14 safe logout operation", async () => {
    render(<PlatformSessions gateway={gateway()} />);

    expect(await screen.findByRole("button", { name: "安全注销" })).toBeDisabled();
    expect(screen.getByText("安全注销将在下一项任务中启用")).toBeVisible();
  });

  it("never reflects gateway error details", async () => {
    const source = gateway();
    vi.mocked(source.getDouyinSession).mockRejectedValue(new Error("/private/profile/secret"));
    render(<PlatformSessions gateway={source} />);

    expect(await screen.findByText("暂时无法读取抖音登录状态，请稍后重试。" )).toBeVisible();
    expect(document.body).not.toHaveTextContent("private/profile");
  });
});
