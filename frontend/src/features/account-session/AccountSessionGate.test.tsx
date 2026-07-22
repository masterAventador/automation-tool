import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AccountSessionGate } from "./AccountSessionGate";
import {
  AccountSessionGatewayError,
  type AccountSessionGateway,
  type AccountSessionSnapshot,
} from "./account-session-gateway";

const unauthenticated: AccountSessionSnapshot = { state: "unauthenticated", account: null };
const authenticated: AccountSessionSnapshot = {
  state: "authenticated",
  account: {
    userId: "123e4567-e89b-42d3-a456-426614174000",
    loginName: "demo.operator",
    status: "active",
  },
};

function gateway(overrides: Partial<AccountSessionGateway> = {}): AccountSessionGateway {
  return {
    restoreSession: vi.fn().mockResolvedValue(unauthenticated),
    login: vi.fn().mockResolvedValue(authenticated),
    recoverPassword: vi.fn().mockResolvedValue(unauthenticated),
    changePassword: vi.fn().mockResolvedValue(unauthenticated),
    logout: vi.fn().mockResolvedValue(unauthenticated),
    ...overrides,
  };
}

describe("customer Demo product account gate", () => {
  it("does not mount the business workbench before a successful login", async () => {
    const accountGateway = gateway();
    const user = userEvent.setup();

    render(
      <AccountSessionGate gateway={accountGateway}>
        <div>受保护工作台</div>
      </AccountSessionGate>,
    );

    expect(await screen.findByRole("heading", { name: "登录自动化运营工具" })).toBeVisible();
    expect(screen.queryByText("受保护工作台")).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("登录名"), "demo.operator");
    await user.type(screen.getByLabelText("密码"), "Correct-Horse-12");
    await user.click(screen.getByRole("button", { name: /登\s*录/u }));

    expect(await screen.findByText("受保护工作台")).toBeVisible();
    expect(screen.getByText("demo.operator")).toBeVisible();
    expect(accountGateway.login).toHaveBeenCalledWith({
      loginName: "demo.operator",
      password: "Correct-Horse-12",
    });
    expect(document.body).not.toHaveTextContent(/atas1|atrs1|Correct-Horse-12/u);
  });

  it("restores a Rust-owned Session after App restart without rendering login", async () => {
    const accountGateway = gateway({ restoreSession: vi.fn().mockResolvedValue(authenticated) });

    render(
      <AccountSessionGate gateway={accountGateway}>
        <div>重启后工作台</div>
      </AccountSessionGate>,
    );

    expect(await screen.findByText("重启后工作台")).toBeVisible();
    expect(screen.queryByRole("heading", { name: "登录自动化运营工具" })).not.toBeInTheDocument();
  });

  it("keeps the workbench unmounted while offline and retries the private restore", async () => {
    const restoreSession = vi
      .fn()
      .mockRejectedValueOnce(new AccountSessionGatewayError("transport_unavailable", true))
      .mockResolvedValueOnce(authenticated);
    const user = userEvent.setup();

    render(
      <AccountSessionGate gateway={gateway({ restoreSession })}>
        <div>恢复后的工作台</div>
      </AccountSessionGate>,
    );

    expect(await screen.findByRole("heading", { name: "暂时无法确认账号状态" })).toBeVisible();
    expect(screen.queryByText("恢复后的工作台")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新检查" }));
    expect(await screen.findByText("恢复后的工作台")).toBeVisible();
    expect(restoreSession).toHaveBeenCalledTimes(2);
  });

  it("uses one enumeration-safe message for invalid, locked, or disabled credentials", async () => {
    const accountGateway = gateway({
      login: vi
        .fn()
        .mockRejectedValue(new AccountSessionGatewayError("authentication_invalid", false)),
    });
    const user = userEvent.setup();
    render(<AccountSessionGate gateway={accountGateway}>工作台</AccountSessionGate>);

    await screen.findByRole("heading", { name: "登录自动化运营工具" });
    await user.type(screen.getByLabelText("登录名"), "demo.operator");
    await user.type(screen.getByLabelText("密码"), "Incorrect-Password-12");
    await user.click(screen.getByRole("button", { name: /登\s*录/u }));

    expect(await screen.findByText("登录信息无效或账号暂不可用")).toBeVisible();
    expect(document.body).not.toHaveTextContent(/不存在|已锁定|已停用/u);
    expect(screen.queryByText("工作台")).not.toBeInTheDocument();
  });

  it("consumes an operations-issued recovery token without exposing it", async () => {
    const accountGateway = gateway();
    const user = userEvent.setup();
    render(<AccountSessionGate gateway={accountGateway}>工作台</AccountSessionGate>);

    await screen.findByRole("heading", { name: "登录自动化运营工具" });
    await user.click(screen.getByRole("button", { name: "使用恢复票据" }));
    await user.type(screen.getByLabelText("恢复票据"), "atrp1.private-recovery-token");
    await user.type(screen.getByLabelText("新密码"), "Recovered-Password-12");
    await user.click(screen.getByRole("button", { name: "重置密码" }));

    expect(await screen.findByText("密码已重置，请使用新密码登录")).toBeVisible();
    expect(accountGateway.recoverPassword).toHaveBeenCalledWith({
      recoveryToken: "atrp1.private-recovery-token",
      newPassword: "Recovered-Password-12",
    });
    expect(document.body).not.toHaveTextContent(/atrp1|Recovered-Password-12/u);
  });

  it("logs out through Rust before unmounting the workbench", async () => {
    const accountGateway = gateway({ restoreSession: vi.fn().mockResolvedValue(authenticated) });
    const user = userEvent.setup();
    render(
      <AccountSessionGate gateway={accountGateway}>
        <div>在线工作台</div>
      </AccountSessionGate>,
    );

    await screen.findByText("在线工作台");
    await user.click(screen.getByRole("button", { name: "退出产品账号" }));

    await waitFor(() => expect(accountGateway.logout).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("heading", { name: "登录自动化运营工具" })).toBeVisible();
    expect(screen.queryByText("在线工作台")).not.toBeInTheDocument();
  });

  it("changes the password through Rust and requires a fresh login", async () => {
    const accountGateway = gateway({ restoreSession: vi.fn().mockResolvedValue(authenticated) });
    const user = userEvent.setup();
    render(
      <AccountSessionGate gateway={accountGateway}>
        <div>改密前工作台</div>
      </AccountSessionGate>,
    );

    await screen.findByText("改密前工作台");
    await user.click(screen.getByRole("button", { name: "修改密码" }));
    await user.type(screen.getByLabelText("当前密码"), "Correct-Horse-12");
    await user.type(screen.getByLabelText("新密码"), "Changed-Password-12");
    await user.click(screen.getByRole("button", { name: "确认修改" }));

    await waitFor(() =>
      expect(accountGateway.changePassword).toHaveBeenCalledWith({
        currentPassword: "Correct-Horse-12",
        newPassword: "Changed-Password-12",
      }),
    );
    expect(await screen.findByText("密码已修改，请重新登录")).toBeVisible();
    expect(screen.queryByText("改密前工作台")).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/Correct-Horse-12|Changed-Password-12/u);
  });
});
