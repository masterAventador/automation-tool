import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { StartupCheck } from "./startup";

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
});
