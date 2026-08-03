import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { BilibiliServiceSettings } from "./BilibiliServiceSettings";
import type {
  BilibiliServiceGateway,
  BilibiliServiceSnapshot,
} from "./bilibili-service-gateway";

const emptySnapshot: BilibiliServiceSnapshot = {
  provider: "bilibili",
  providerLabel: "B站开放平台",
  configured: false,
  targetAccount: null,
  tid: null,
  tag: null,
  noReprint: null,
};

const configuredSnapshot: BilibiliServiceSnapshot = {
  ...emptySnapshot,
  configured: true,
  targetAccount: "运营账号",
  tid: 171,
  tag: "自动化,效率",
  noReprint: 1,
};

function gateway(): BilibiliServiceGateway {
  return {
    getSettings: vi.fn().mockResolvedValue(emptySnapshot),
    configure: vi.fn().mockResolvedValue(configuredSnapshot),
    clear: vi.fn().mockResolvedValue(emptySnapshot),
  };
}

describe("Bilibili service settings", () => {
  it("saves all fields, clears secrets, and only renders public defaults", async () => {
    const source = gateway();
    const user = userEvent.setup();
    render(<BilibiliServiceSettings gateway={source} />);

    await screen.findByText("未配置");
    await user.type(screen.getByLabelText("B站 Client ID"), "client-1");
    await user.type(screen.getByLabelText("B站 App Secret"), "app-secret");
    await user.type(screen.getByLabelText("B站访问凭证"), "access-token");
    await user.type(screen.getByLabelText("B站更新凭证"), "refresh-token");
    await user.type(screen.getByLabelText("B站授权有效期"), "2030-01-01T00:00");
    await user.type(screen.getByLabelText("B站目标账号"), "运营账号");
    await user.type(screen.getByLabelText("B站投稿分区 ID"), "171");
    await user.type(screen.getByLabelText("B站投稿标签"), "自动化,效率");
    await user.click(screen.getByRole("button", { name: "保存配置" }));

    expect(source.configure).toHaveBeenCalledWith(
      expect.objectContaining({
        clientId: "client-1",
        appSecret: "app-secret",
        accessToken: "access-token",
        refreshToken: "refresh-token",
        targetAccount: "运营账号",
        tid: 171,
        tag: "自动化,效率",
        noReprint: 1,
      }),
    );
    expect(await screen.findByText("已配置")).toBeVisible();
    expect(screen.getByLabelText("B站 App Secret")).toHaveValue("");
    expect(document.body.textContent).not.toContain("app-secret");
    expect(document.body.textContent).not.toContain("access-token");

    await user.click(screen.getByRole("button", { name: "清除配置" }));

    expect(source.clear).toHaveBeenCalledOnce();
    expect(await screen.findByText("未配置")).toBeVisible();
    expect(screen.getByLabelText("B站目标账号")).toHaveValue("");
    expect(screen.getByLabelText("B站投稿分区 ID")).toHaveValue("");
    expect(screen.getByLabelText("B站投稿标签")).toHaveValue("");
  });
});
