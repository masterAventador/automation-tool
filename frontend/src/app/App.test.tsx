import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { StartupCheck } from "./startup";
import type { PlatformAdapter } from "../platform/types";
import type { PlatformSessionGateway } from "../features/platform-sessions/platform-session-gateway";
import type { AppUpdateGateway } from "../features/app-updates/contracts";
import type { AccountSessionGateway } from "../features/account-session/account-session-gateway";

describe("desktop startup", () => {
  it("keeps customer Demo startup and workbench unmounted until product login", async () => {
    const startupCheck: StartupCheck = {
      check: vi.fn().mockResolvedValue({ status: "ready" as const }),
    };
    const accountSessionGateway: AccountSessionGateway = {
      restoreSession: vi.fn().mockResolvedValue({ state: "unauthenticated", account: null }),
      login: vi.fn(),
      recoverPassword: vi.fn(),
      changePassword: vi.fn(),
      logout: vi.fn(),
      listDevices: vi.fn().mockResolvedValue([]),
      revokeDevice: vi.fn(),
    };

    render(
      <App startupCheck={startupCheck} accountSessionGateway={accountSessionGateway} />,
    );

    expect(await screen.findByRole("heading", { name: "登录自动化运营工具" })).toBeVisible();
    expect(startupCheck.check).not.toHaveBeenCalled();
    expect(screen.queryByRole("heading", { name: "RPA 运营工作台" })).not.toBeInTheDocument();
  });

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
    expect(screen.getByText("控制服务不可用")).toBeVisible();
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

  it("shows every local startup failure and opens only the existing safe repair tools", async () => {
    const startupCheck: StartupCheck = {
      check: vi.fn().mockResolvedValue({
        status: "blocked" as const,
        diagnostics: [
          "executor_configuration_required" as const,
          "browser_component_damaged" as const,
          "app_data_unavailable" as const,
        ],
      }),
    };
    const platformAdapter: PlatformAdapter = {
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
    const user = userEvent.setup();

    render(<App startupCheck={startupCheck} platformAdapter={platformAdapter} />);

    expect(
      await screen.findByRole("heading", { name: "桌面运行环境需要处理" }),
    ).toBeVisible();
    expect(screen.getByText("本地执行器动作配置缺失")).toBeVisible();
    expect(screen.getByText("浏览器组件损坏")).toBeVisible();
    expect(screen.getByText("App 私有数据目录不可用")).toBeVisible();
    expect(document.body).not.toHaveTextContent(/\/Users\/|token=|私钥内容/iu);

    await user.click(screen.getByRole("button", { name: "打开本地修复工具" }));

    // EB-10：浏览器选择面板已从修复工具移除，内置浏览器无需选择。
    expect(screen.queryByText("尚未选择运营浏览器")).toBeNull();
    expect(screen.queryByText(/Chrome|Edge/u)).toBeNull();
    expect(await screen.findByRole("heading", { name: "本地执行器已停止" })).toBeVisible();
  });

  it("opens settings and diagnostics through the injected PlatformAdapter", async () => {
    const startupCheck: StartupCheck = {
      check: vi.fn().mockResolvedValue({ status: "ready" as const }),
    };
    const platformAdapter: PlatformAdapter = {
      getExecutorStatus: vi.fn().mockResolvedValue({
        state: "running",
        version: "0.1.0",
        buildId: "app-test",
        restartCount: 0,
      }),
      restartExecutor: vi.fn(),
      getExecutorDiagnostics: vi.fn().mockResolvedValue(["safe app diagnostic"]),
      exportDiagnostics: vi.fn(),
      emergencyStopExecutor: vi.fn(),
      getBrowserDiagnosticSettings: vi.fn().mockResolvedValue({ captureSuccessfulRuns: false }),
      setCaptureSuccessfulDiagnostics: vi.fn(),
    };
    const appUpdateGateway: AppUpdateGateway = {
      getState: vi.fn().mockResolvedValue({ state: "up_to_date", trigger: "manual" }),
      checkNow: vi.fn().mockResolvedValue({ state: "up_to_date", trigger: "manual" }),
      decide: vi.fn(),
    };
    const user = userEvent.setup();

    render(
      <App
        startupCheck={startupCheck}
        platformAdapter={platformAdapter}
        appUpdateGateway={appUpdateGateway}
      />,
    );

    await screen.findByRole("heading", { name: "RPA 运营工作台" });
    await user.click(screen.getByRole("menuitem", { name: "设置与诊断" }));
    expect(await screen.findByRole("heading", { name: "设置与诊断" })).toBeVisible();
    expect(await screen.findByText("本地执行器运行中")).toBeVisible();
    expect(screen.getByText("safe app diagnostic")).toBeVisible();
    expect(screen.getByRole("heading", { name: "App 更新" })).toBeVisible();
    expect(screen.getByText("当前已是最新版本")).toBeVisible();
    expect(platformAdapter.getExecutorStatus).toHaveBeenCalledTimes(1);
    expect(appUpdateGateway.getState).toHaveBeenCalledTimes(1);
  });

  /**
   * `App` is the second assembly entry: everything a user actually launches
   * (`main.tsx`, the Tauri test entry, the harness) goes through it, never
   * through `WorkbenchShell` directly. The shell's own test proves the shell;
   * this proves the composition around it did not put the demoted notice back
   * on the sidebar, or hide the settings entry that replaced it.
   */
  it("keeps the open source licence notice out of the assembled main navigation", async () => {
    const startupCheck: StartupCheck = {
      check: vi.fn().mockResolvedValue({ status: "ready" as const }),
    };

    render(<App startupCheck={startupCheck} />);

    await screen.findByRole("heading", { name: "RPA 运营工作台" });
    const navigation = screen.getByRole("navigation", { name: "桌面主导航" });
    expect(
      within(navigation).queryByRole("menuitem", { name: "第三方软件声明" }),
    ).not.toBeInTheDocument();
    expect(
      within(navigation).queryByRole("menuitem", { name: "开源软件许可" }),
    ).not.toBeInTheDocument();
  });

  it("opens the open source licence notice from the foot of settings and diagnostics", async () => {
    const startupCheck: StartupCheck = {
      check: vi.fn().mockResolvedValue({ status: "ready" as const }),
    };
    const platformAdapter: PlatformAdapter = {
      getExecutorStatus: vi.fn().mockResolvedValue({
        state: "running",
        version: "0.1.0",
        buildId: "app-test",
        restartCount: 0,
      }),
      restartExecutor: vi.fn(),
      getExecutorDiagnostics: vi.fn().mockResolvedValue([]),
      exportDiagnostics: vi.fn(),
      emergencyStopExecutor: vi.fn(),
      getBrowserDiagnosticSettings: vi.fn().mockResolvedValue({ captureSuccessfulRuns: false }),
      setCaptureSuccessfulDiagnostics: vi.fn(),
    };
    const user = userEvent.setup();

    render(<App startupCheck={startupCheck} platformAdapter={platformAdapter} />);

    await screen.findByRole("heading", { name: "RPA 运营工作台" });
    await user.click(screen.getByRole("menuitem", { name: "设置与诊断" }));
    await user.click(await screen.findByRole("button", { name: "开源软件许可" }));

    expect(screen.getByRole("heading", { name: "开源软件许可" })).toBeVisible();
    expect(screen.getByRole("region", { name: "上游开源项目" })).toBeVisible();
    expect(screen.getByRole("region", { name: "字体与素材权利" })).toBeVisible();
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
      logoutDouyinSession: vi.fn(),
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
