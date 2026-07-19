import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { StartupCheck } from "./startup";
import type { PlatformAdapter } from "../platform/types";
import type { PlatformSessionGateway } from "../features/platform-sessions/platform-session-gateway";

describe("desktop startup", () => {
  it("opens the RPA workbench without any product login route", async () => {
    const startupCheck: StartupCheck = {
      check: vi.fn().mockResolvedValue({ status: "ready" as const }),
    };

    render(<App startupCheck={startupCheck} />);

    expect(await screen.findByRole("heading", { name: "RPA 运营工作台" })).toBeVisible();
    expect(screen.getByRole("navigation", { name: "桌面主导航" })).toBeVisible();
    expect(document.body).not.toHaveTextContent(/产品登录|注册账号|账号登录/);
    expect(startupCheck.check).toHaveBeenCalledTimes(1);
  });

  it("shows a safe diagnostic state when Control Plane is unavailable and can retry", async () => {
    const startupCheck: StartupCheck = {
      check: vi
        .fn()
        .mockResolvedValueOnce({ status: "unavailable" as const })
        .mockResolvedValueOnce({ status: "ready" as const }),
    };
    const user = userEvent.setup();

    render(<App startupCheck={startupCheck} />);

    expect(
      await screen.findByRole("heading", { name: "暂时无法连接业务服务" }),
    ).toBeVisible();
    expect(screen.getByText("Control Plane 不可用")).toBeVisible();
    expect(screen.queryByRole("button", { name: /登录/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重新检查" }));

    expect(await screen.findByRole("heading", { name: "RPA 运营工作台" })).toBeVisible();
    expect(startupCheck.check).toHaveBeenCalledTimes(2);
  });

  it("does not reveal unexpected startup exception details", async () => {
    const startupCheck: StartupCheck = {
      check: vi.fn().mockRejectedValue(new Error("password=private-startup-secret")),
    };

    render(<App startupCheck={startupCheck} />);

    expect(
      await screen.findByRole("heading", { name: "暂时无法连接业务服务" }),
    ).toBeVisible();
    expect(document.body).not.toHaveTextContent("private-startup-secret");
  });

  it("shows a distinct safe diagnostic when the Installation is revoked", async () => {
    const startupCheck: StartupCheck = {
      check: vi.fn().mockResolvedValue({ status: "revoked" as const }),
    };

    render(<App startupCheck={startupCheck} />);

    expect(
      await screen.findByRole("heading", { name: "当前安装实例已失效" }),
    ).toBeVisible();
    expect(screen.getByText("安装实例授权不可用")).toBeVisible();
    expect(document.body).not.toHaveTextContent(/账号登录|注册账号|私钥|凭据/);
  });

  it("opens settings and diagnostics through the injected PlatformAdapter", async () => {
    const startupCheck: StartupCheck = {
      check: vi.fn().mockResolvedValue({ status: "ready" as const }),
    };
    const platformAdapter: PlatformAdapter = {
      getBrowserSettings: vi.fn().mockResolvedValue({
        availableBrowsers: ["google_chrome"],
        selectedBrowser: "google_chrome",
      }),
      selectBrowser: vi.fn(),
      getExecutorStatus: vi.fn().mockResolvedValue({
        state: "running",
        version: "0.1.0",
        buildId: "app-test",
        restartCount: 0,
      }),
      restartExecutor: vi.fn(),
      getExecutorDiagnostics: vi.fn().mockResolvedValue(["safe app diagnostic"]),
      emergencyStopExecutor: vi.fn(),
    };
    const user = userEvent.setup();

    render(<App startupCheck={startupCheck} platformAdapter={platformAdapter} />);

    await screen.findByRole("heading", { name: "RPA 运营工作台" });
    await user.click(screen.getByRole("menuitem", { name: "设置与诊断" }));
    expect(await screen.findByRole("heading", { name: "设置与诊断" })).toBeVisible();
    expect(await screen.findByText("本地执行器运行中")).toBeVisible();
    expect(screen.getByText("safe app diagnostic")).toBeVisible();
    expect(platformAdapter.getExecutorStatus).toHaveBeenCalledTimes(1);
  });

  it("opens the enabled platform status page through the injected real gateway boundary", async () => {
    const startupCheck: StartupCheck = {
      check: vi.fn().mockResolvedValue({ status: "ready" as const }),
    };
    const platformSessionGateway: PlatformSessionGateway = {
      getDouyinSession: vi.fn().mockResolvedValue({
        platform: "douyin",
        state: "healthy",
        observedAt: "2026-07-19T14:30:00Z",
      }),
      openDouyinLogin: vi.fn(),
      recheckDouyinLogin: vi.fn(),
    };
    const user = userEvent.setup();

    render(
      <App
        startupCheck={startupCheck}
        platformSessionGateway={platformSessionGateway}
      />,
    );

    await screen.findByRole("heading", { name: "RPA 运营工作台" });
    await user.click(screen.getByRole("menuitem", { name: "平台状态" }));
    expect(await screen.findByRole("heading", { name: "平台状态" })).toBeVisible();
    expect(await screen.findByText("登录正常")).toBeVisible();
    expect(platformSessionGateway.getDouyinSession).toHaveBeenCalledOnce();
  });
});
