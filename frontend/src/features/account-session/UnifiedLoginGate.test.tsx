import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { TauriAuthClientApi } from "@unified-login/tauri";

import { UnifiedLoginGate } from "./UnifiedLoginGate";

function clientWith(overrides: Partial<TauriAuthClientApi>): TauriAuthClientApi {
  return {
    login: vi.fn().mockResolvedValue(undefined),
    logout: vi.fn().mockResolvedValue(undefined),
    getAccessToken: vi.fn().mockRejectedValue(new Error("login required")),
    onAuthStateChange: vi.fn().mockReturnValue(() => {}),
    ...overrides,
  };
}

describe("UnifiedLoginGate", () => {
  it("未登录时挡住工作台并给出浏览器登录入口", async () => {
    const client = clientWith({});
    render(
      <UnifiedLoginGate client={client}>
        <div>工作台内容</div>
      </UnifiedLoginGate>,
    );
    expect(await screen.findByRole("button", { name: "用浏览器登录" })).toBeVisible();
    expect(screen.queryByText("工作台内容")).not.toBeInTheDocument();
  });

  it("已有令牌时直接放行工作台", async () => {
    const client = clientWith({
      getAccessToken: vi.fn().mockResolvedValue("token"),
    });
    render(
      <UnifiedLoginGate client={client}>
        <div>工作台内容</div>
      </UnifiedLoginGate>,
    );
    expect(await screen.findByText("工作台内容")).toBeVisible();
  });

  it("点击登录调用统一登录并在成功后放行", async () => {
    const client = clientWith({});
    render(
      <UnifiedLoginGate client={client}>
        <div>工作台内容</div>
      </UnifiedLoginGate>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "用浏览器登录" }));
    expect(client.login).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("工作台内容")).toBeVisible();
  });

  it("退出登录后回到登录页", async () => {
    const client = clientWith({
      getAccessToken: vi.fn().mockResolvedValue("token"),
    });
    render(
      <UnifiedLoginGate client={client}>
        <div>工作台内容</div>
      </UnifiedLoginGate>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "退出登录" }));
    expect(client.logout).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("button", { name: "用浏览器登录" })).toBeVisible();
  });
});
