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
    expect(screen.queryByRole("heading", { name: "AI 运营助理" })).not.toBeInTheDocument();
  });

  it("opens the RPA workbench without any product login route", async () => {
    const startupCheck: StartupCheck = {
      check: vi.fn().mockResolvedValue({ status: "ready" as const }),
    };

    render(<App startupCheck={startupCheck} />);

    expect(await screen.findByRole("heading", { name: "AI 运营助理" })).toBeVisible();
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

    expect(await screen.findByRole("heading", { name: "AI 运营助理" })).toBeVisible();
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

    await screen.findByRole("heading", { name: "AI 运营助理" });
    await user.click(screen.getByRole("menuitem", { name: "设置" }));
    expect(await screen.findByRole("heading", { name: "设置", level: 2 })).toBeVisible();
    expect(await screen.findByText("本地执行器运行中")).toBeVisible();
    expect(screen.getByText("safe app diagnostic")).toBeVisible();
    expect(screen.getByRole("heading", { name: "App 更新" })).toBeVisible();
    expect(screen.getByText("当前已是最新版本")).toBeVisible();
    expect(platformAdapter.getExecutorStatus).toHaveBeenCalledTimes(1);
    // Twice, and both are wanted. The shell mounts its own `AppUpdateCenter`
    // while the user is anywhere but 设置, because the update prompt has to
    // exist wherever they are standing — a forced update the user only learns
    // about by opening settings is one they can decline forever by not going
    // there. That instance asks once at startup; the settings page's own
    // instance asks again when it mounts, which is also what it did before.
    //
    // What this assertion guards is unchanged: neither instance polls or
    // re-asks while it is on screen.
    expect(appUpdateGateway.getState).toHaveBeenCalledTimes(2);
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

    await screen.findByRole("heading", { name: "AI 运营助理" });
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

    await screen.findByRole("heading", { name: "AI 运营助理" });
    await user.click(screen.getByRole("menuitem", { name: "设置" }));
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

    await screen.findByRole("heading", { name: "AI 运营助理" });
    await user.click(screen.getByRole("menuitem", { name: "账号与平台" }));
    expect(
      await screen.findByRole("heading", { name: "账号与平台", level: 2 }),
    ).toBeVisible();
    expect(await screen.findByText("登录正常")).toBeVisible();
    expect(platformSessionGateway.getDouyinSession).toHaveBeenCalledOnce();
  });
  it("explains a device registration conflict instead of blaming the local environment", async () => {
    const startupCheck: StartupCheck = {
      check: vi.fn().mockResolvedValue({
        status: "blocked" as const,
        diagnostics: ["installation_conflict" as const],
      }),
    };

    render(<App startupCheck={startupCheck} />);

    expect(
      await screen.findByRole("heading", { name: "本机设备注册需要重置" }),
    ).toBeVisible();
    expect(screen.getByText("本机安装记录与业务服务不一致")).toBeVisible();
    expect(screen.queryByRole("button", { name: "打开本地修复工具" })).toBeNull();
    expect(document.body).not.toHaveTextContent(/\/Users\/|atb1\.|token=/iu);
  });
});

describe("update reachability from the startup repair screen", () => {
  // 更新器替换的是整个 .app（含资源）——它本身就是损坏安装的标准自愈通道。
  // 启动被挡在修复页时，更新提示与手动检查都必须仍然可达，否则坏掉的安装
  // 永远收不到能修好它的那个版本。
  const blockedStartup = (): StartupCheck => ({
    check: vi.fn().mockResolvedValue({
      status: "blocked" as const,
      diagnostics: ["browser_component_damaged" as const],
    }),
  });

  function promptingGateway(): AppUpdateGateway {
    const release = {
      version: "0.2.0",
      channel: "stable",
      policy: "optional",
      notes: "",
      publishedAt: "2026-07-29T00:00:00Z",
      artifact: {
        target: "darwin",
        arch: "aarch64",
        sha256: "b".repeat(64),
        sizeBytes: 2048,
      },
    } as const;
    return {
      getState: vi.fn().mockResolvedValue({ state: "ready", release, action: "prompt" }),
      checkNow: vi.fn().mockResolvedValue({ state: "ready", release, action: "prompt" }),
      decide: vi.fn().mockResolvedValue({ state: "installation_launched", release }),
    };
  }

  it("shows the update prompt on the repair screen without opening anything", async () => {
    render(<App startupCheck={blockedStartup()} appUpdateGateway={promptingGateway()} />);

    expect(
      await screen.findByRole("heading", { name: "桌面运行环境需要处理" }),
    ).toBeVisible();
    // App 自带 ConfigProvider，测试注不进 motion:false，antd Modal 在 jsdom 里
    // 停在动画态导致 toBeVisible 不稳定；断言落在提示的决策按钮可用上。
    await screen.findByRole("heading", { name: "发现新版本 0.2.0" });
    expect(await screen.findByRole("button", { name: "稍后提醒" })).toBeEnabled();
  });

  it("offers 检查更新 inside the repair tools", async () => {
    const user = userEvent.setup();
    render(<App startupCheck={blockedStartup()} appUpdateGateway={promptingGateway()} />);

    await screen.findByRole("heading", { name: "桌面运行环境需要处理" });
    await user.click(screen.getByRole("button", { name: "打开本地修复工具" }));

    expect(await screen.findByRole("button", { name: "检查更新" })).toBeVisible();
  });
});
