import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { VideoEditingServiceSettings } from "./VideoEditingServiceSettings";
import {
  VideoEditingServiceGatewayError,
  type VideoEditingServiceGateway,
  type VideoEditingServiceSnapshot,
} from "./video-editing-service-gateway";

const emptySnapshot: VideoEditingServiceSnapshot = {
  provider: "aliyun_ims",
  providerLabel: "阿里云视频剪辑服务",
  catalogVerifiedAt: "2026-07-23",
  configured: false,
  region: null,
};

const configuredSnapshot: VideoEditingServiceSnapshot = {
  ...emptySnapshot,
  configured: true,
  region: "cn-shanghai",
};

function gateway(
  overrides: Partial<VideoEditingServiceGateway> = {},
): VideoEditingServiceGateway {
  return {
    getSettings: vi.fn().mockResolvedValue(emptySnapshot),
    configure: vi.fn().mockResolvedValue(configuredSnapshot),
    clear: vi.fn().mockResolvedValue(emptySnapshot),
    testConnection: vi
      .fn()
      .mockResolvedValue({ region: "cn-shanghai", status: "connected" }),
    ...overrides,
  };
}

const SECRET = "ve04UiSecretValue1234567890";

describe("video editing service settings", () => {
  it("saves credentials, clears inputs and never shows a stored secret", async () => {
    const editingGateway = gateway();
    const user = userEvent.setup();
    render(<VideoEditingServiceSettings gateway={editingGateway} />);

    await screen.findByText("视频剪辑服务");
    expect(screen.getByText("未配置")).toBeInTheDocument();

    const keyIdInput = screen.getByLabelText("阿里云 AccessKey ID");
    const secretInput = screen.getByLabelText("阿里云 AccessKey Secret");
    await user.type(keyIdInput, "LTAI5tVe04TestAccessKey");
    await user.type(secretInput, SECRET);
    await user.click(screen.getByRole("button", { name: "保存配置" }));

    expect(editingGateway.configure).toHaveBeenCalledWith({
      region: "cn-shanghai",
      accessKeyId: "LTAI5tVe04TestAccessKey",
      accessKeySecret: SECRET,
    });
    expect(await screen.findByText("已配置")).toBeInTheDocument();
    expect(keyIdInput).toHaveValue("");
    expect(secretInput).toHaveValue("");
    expect(document.body.textContent).not.toContain(SECRET);
  });

  it("requires new credentials before saving", async () => {
    const editingGateway = gateway();
    const user = userEvent.setup();
    render(<VideoEditingServiceSettings gateway={editingGateway} />);

    await screen.findByText("视频剪辑服务");
    await user.click(screen.getByRole("button", { name: "保存配置" }));

    expect(editingGateway.configure).not.toHaveBeenCalled();
    expect(
      await screen.findByText("请输入新的 AccessKey ID 和 AccessKey Secret。已保存的密钥不会回显。"),
    ).toBeInTheDocument();
  });

  it("shows sanitized fixed errors for failed connection tests", async () => {
    const editingGateway = gateway({
      getSettings: vi.fn().mockResolvedValue(configuredSnapshot),
      testConnection: vi
        .fn()
        .mockRejectedValue(new VideoEditingServiceGatewayError("permission_denied", false)),
    });
    const user = userEvent.setup();
    render(<VideoEditingServiceSettings gateway={editingGateway} />);

    await screen.findByText("已配置");
    await user.click(screen.getByRole("button", { name: "测试连接" }));

    expect(
      await screen.findByText("当前访问密钥缺少视频剪辑所需权限，请检查授权后重试。"),
    ).toBeInTheDocument();
  });

  it("reports a successful connection test and supports clearing", async () => {
    const editingGateway = gateway({
      getSettings: vi.fn().mockResolvedValue(configuredSnapshot),
    });
    const user = userEvent.setup();
    render(<VideoEditingServiceSettings gateway={editingGateway} />);

    await screen.findByText("已配置");
    await user.click(screen.getByRole("button", { name: "测试连接" }));
    expect(await screen.findByText("连接成功；访问密钥与所选地域可用。")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "清除配置" }));
    expect(editingGateway.clear).toHaveBeenCalled();
    expect(await screen.findByText("未配置")).toBeInTheDocument();
  });
});
