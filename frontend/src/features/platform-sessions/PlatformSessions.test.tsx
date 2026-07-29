import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  PlatformSessionGatewayError,
  type PlatformSessionGateway,
  type PlatformSessionGatewayErrorCode,
} from "./platform-session-gateway";
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

  /**
   * T109: a user on a signed build pressed both buttons and got one sentence —
   * "暂时无法读取抖音登录状态，请稍后重试" — for every possible cause, because the
   * handler's `catch {}` discarded the error. Six hours of forensics later the
   * reason still could not be named. These cases pin the three things the page
   * now has to distinguish, and above all that a fault in our own program is
   * never dressed up as something the operator should retry.
   */
  it("says a packaged-component fault is ours and does not ask the user to retry", async () => {
    const source = gateway();
    vi.mocked(source.openDouyinLogin).mockRejectedValue(
      new PlatformSessionGatewayError("browser_component_missing", false),
    );
    const user = userEvent.setup();
    render(<PlatformSessions gateway={source} />);

    await user.click(await screen.findByRole("button", { name: "打开登录处理" }));

    expect(await screen.findByText(/运营浏览器组件缺失或损坏/u)).toBeVisible();
    expect(screen.getByText(/browser_component_missing/u)).toBeVisible();
    expect(document.body).not.toHaveTextContent("请稍后重试");
  });

  it("offers a retry only for a genuinely temporary local failure", async () => {
    const source = gateway();
    vi.mocked(source.recheckDouyinLogin).mockRejectedValue(
      new PlatformSessionGatewayError("process_unavailable", true),
    );
    const user = userEvent.setup();
    render(<PlatformSessions gateway={source} />);

    await user.click(await screen.findByRole("button", { name: "我已处理，重新检查" }));

    expect(await screen.findByText(/本机执行器暂时不可用/u)).toBeVisible();
    expect(screen.getByText(/process_unavailable/u)).toBeVisible();
  });

  it("tells the operator what to do when the operations browser is already in use", async () => {
    const source = gateway();
    vi.mocked(source.openDouyinLogin).mockRejectedValue(
      new PlatformSessionGatewayError("profile_in_use", true),
    );
    const user = userEvent.setup();
    render(<PlatformSessions gateway={source} />);

    await user.click(await screen.findByRole("button", { name: "打开登录处理" }));

    expect(await screen.findByText(/运营浏览器正在被占用/u)).toBeVisible();
  });

  /**
   * The local check succeeded; only the authoritative server projection lagged.
   * Reporting that as "cannot read the login state" would describe the opposite
   * of what happened, and would send the user back to press the same button.
   */
  it("distinguishes a lagging server projection from a failed local check", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const source = gateway();
      vi.mocked(source.getDouyinSession).mockResolvedValue({
        platform: "douyin",
        state: "missing",
        observedAt: "2026-07-19T14:30:00Z",
      });
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<PlatformSessions gateway={source} />);

      await user.click(await screen.findByRole("button", { name: "我已处理，重新检查" }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });

      expect(screen.getByText(/本机已确认登录正常/u)).toBeVisible();
      expect(screen.getByText(/health_publication_timed_out/u)).toBeVisible();
    } finally {
      vi.useRealTimers();
    }
  });
});

/**
 * PC-25：拆开的 Profile 失败码必须各自有话说。
 *
 * 五种 Profile 失败此前在 Rust 侧一起塌成 `storage_unavailable`，界面对每
 * 一种都说「重新操作不会有效」。Rust 侧拆开之后，界面若不认这些新码就会
 * 掉进 default 分支——用户看到的还是同一句万能话，拆开等于白拆。
 */
describe("PC-25 profile removal failure copy", () => {
  it("names each profile failure with its own operator-facing sentence", async () => {
    const cases: ReadonlyArray<readonly [PlatformSessionGatewayErrorCode, RegExp]> = [
      ["profile_identity_changed", /运营浏览器档案被系统外的东西改动过/u],
      ["profile_missing", /运营浏览器档案已经不在了/u],
      ["profile_directory_unsafe", /运营浏览器档案所在目录不安全/u],
      ["profile_marker_invalid", /运营浏览器档案记录读不出来/u],
    ];
    for (const [code, expected] of cases) {
      const source = gateway();
      vi.mocked(source.logoutDouyinSession).mockRejectedValue(
        new PlatformSessionGatewayError(code, false),
      );
      const user = userEvent.setup();
      const view = render(<PlatformSessions gateway={source} />);

      await user.click(await screen.findByRole("button", { name: "安全注销" }));
      await user.click(screen.getByRole("button", { name: "确认注销" }));

      expect(await screen.findByText(expected)).toBeVisible();
      expect(screen.getByText(new RegExp(code, "u"))).toBeVisible();
      view.unmount();
    }
  });
});
