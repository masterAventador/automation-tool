import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { describe, expect, it, vi } from "vitest";

import type { PlatformSessionGateway } from "./platform-session-gateway";
import { PlatformSessions } from "./PlatformSessions";

function gateway(): PlatformSessionGateway {
  let state: "healthy" | "missing" = "missing";
  return {
    getDouyinSession: vi.fn(async () => ({
      platform: "douyin" as const,
      state,
      observedAt: "2026-07-19T14:30:00Z",
    })),
    openDouyinLogin: vi.fn().mockResolvedValue({
      platform: "douyin",
      state: "awaiting_scan",
      flowVersion: "douyin.qr-login.v2",
      confirmationId: null,
      targetAccount: null,
    }),
    recheckDouyinLogin: vi.fn(async () => {
      state = "healthy";
      return {
        platform: "douyin" as const,
        state: "healthy" as const,
        flowVersion: "douyin.qr-login.v2" as const,
        confirmationId: null,
        targetAccount: null,
      };
    }),
    logoutDouyinSession: vi.fn(async () => {
      state = "missing";
      return {
        platform: "douyin" as const,
        state: "missing" as const,
        observedAt: "2026-07-19T14:31:00Z",
      };
    }),
  };
}

describe("platform status page", () => {
  it("uses the existing login action once for an automatic task handoff", async () => {
    const source = gateway();
    const onAutoOpenConsumed = vi.fn();

    render(
      <PlatformSessions
        gateway={source}
        autoOpenLogin
        onAutoOpenConsumed={onAutoOpenConsumed}
      />,
    );

    expect(await screen.findByText("请在打开的运营浏览器中扫码登录。")).toBeVisible();
    expect(source.openDouyinLogin).toHaveBeenCalledOnce();
    expect(onAutoOpenConsumed).toHaveBeenCalledOnce();
  });

  it("does not duplicate the automatic login action in React StrictMode", async () => {
    const source = gateway();
    const onAutoOpenConsumed = vi.fn();

    render(
      <StrictMode>
        <PlatformSessions
          gateway={source}
          autoOpenLogin
          onAutoOpenConsumed={onAutoOpenConsumed}
        />
      </StrictMode>,
    );

    expect(await screen.findByText("请在打开的运营浏览器中扫码登录。")).toBeVisible();
    expect(source.openDouyinLogin).toHaveBeenCalledOnce();
    expect(onAutoOpenConsumed).toHaveBeenCalledOnce();
  });

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
    expect((await screen.findAllByText("登录正常")).length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText("需要登录")).not.toBeInTheDocument();
    expect(source.getDouyinSession).toHaveBeenCalledTimes(3);
  });

  it("requires confirmation and renders only the authoritative safe logout result", async () => {
    const source = gateway();
    vi.mocked(source.getDouyinSession).mockResolvedValue({
      platform: "douyin",
      state: "healthy",
      observedAt: "2026-07-19T14:30:00Z",
    });
    const user = userEvent.setup();
    render(<PlatformSessions gateway={source} />);

    expect(await screen.findByText("登录正常")).toBeVisible();
    await user.click(await screen.findByRole("button", { name: "安全注销" }));
    expect(source.logoutDouyinSession).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "确认注销" }));

    expect(source.logoutDouyinSession).toHaveBeenCalledOnce();
    expect(await screen.findByText("需要登录")).toBeVisible();
    expect(screen.getByText(/最近检查/u)).toBeVisible();
  });

  it("never reflects gateway error details", async () => {
    const source = gateway();
    vi.mocked(source.getDouyinSession).mockRejectedValue(new Error("/private/profile/secret"));
    render(<PlatformSessions gateway={source} />);

    expect(await screen.findByText("暂时无法读取抖音登录状态，请稍后重试。" )).toBeVisible();
    expect(document.body).not.toHaveTextContent("private/profile");
  });
});
