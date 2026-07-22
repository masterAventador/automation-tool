import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ModelServiceSettings } from "./ModelServiceSettings";
import {
  ModelServiceGatewayError,
  type ModelServiceGateway,
  type ModelServiceSnapshot,
} from "./model-service-gateway";

const emptySnapshot: ModelServiceSnapshot = {
  provider: "bailian",
  providerLabel: "阿里百炼",
  catalogVerifiedAt: "2026-07-23",
  script: {
    purpose: "script",
    configured: false,
    modelId: "qwen3.7-max-2026-06-08",
  },
  videoCreative: {
    purpose: "video_creative",
    configured: false,
    modelId: "qwen3.7-max-2026-06-08",
  },
  sameCredential: false,
};

function gateway(): ModelServiceGateway {
  return {
    getSettings: vi.fn().mockResolvedValue(emptySnapshot),
    configure: vi.fn().mockImplementation(async (input) => ({
      ...emptySnapshot,
      [input.purpose === "script" ? "script" : "videoCreative"]: {
        purpose: input.purpose,
        configured: true,
        modelId: input.modelId,
      },
    })),
    reuseScriptForVideo: vi.fn().mockResolvedValue({
      ...emptySnapshot,
      script: { ...emptySnapshot.script, configured: true },
      videoCreative: { ...emptySnapshot.videoCreative, configured: true },
      sameCredential: true,
    }),
    clear: vi.fn().mockResolvedValue(emptySnapshot),
    testConnection: vi.fn().mockResolvedValue({
      purpose: "script",
      modelId: "qwen3.7-max-2026-06-08",
      status: "connected",
      quota: { remainingRequests: 42, remainingTokens: 1234 },
    }),
  };
}

function purposeSection(title: string): HTMLElement {
  const section = screen.getByRole("heading", { name: title }).closest("section");
  if (section === null) {
    throw new Error("purpose section missing");
  }
  return section;
}

describe("model service settings", () => {
  it("saves a new secret without ever showing a stored credential", async () => {
    const modelGateway = gateway();
    const user = userEvent.setup();
    render(<ModelServiceSettings gateway={modelGateway} />);

    await screen.findByText("文案模型服务");
    const section = purposeSection("文案模型服务");
    const secret = "sk-vf05-ui-private-key-1234567890";
    await user.type(within(section).getByLabelText("文案模型服务 API Key"), secret);
    await user.click(within(section).getByRole("button", { name: "保存配置" }));

    expect(modelGateway.configure).toHaveBeenCalledWith({
      purpose: "script",
      modelId: "qwen3.7-max-2026-06-08",
      apiKey: secret,
    });
    expect(await within(section).findByText("已配置")).toBeVisible();
    expect(within(section).getByLabelText("文案模型服务 API Key")).toHaveValue("");
    expect(document.body).not.toHaveTextContent(secret);
  });

  it("makes credential reuse explicit and keeps the two permission descriptions separate", async () => {
    const modelGateway = gateway();
    vi.mocked(modelGateway.getSettings).mockResolvedValue({
      ...emptySnapshot,
      script: { ...emptySnapshot.script, configured: true },
    });
    const user = userEvent.setup();
    render(<ModelServiceSettings gateway={modelGateway} />);

    expect(
      await screen.findByText("用于文案、脚本和分镜，不会获得浏览器、发布平台或视频文件权限。"),
    ).toBeVisible();
    expect(
      screen.getByText(
        "用于生成和修正视频画面代码，可读取明确提交的预览图，但不会获得运营浏览器或发布平台权限。",
      ),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "视频创作复用文案服务密钥" }));
    expect(modelGateway.reuseScriptForVideo).toHaveBeenCalledTimes(1);
    expect(await within(purposeSection("视频创作模型服务")).findByText("已配置")).toBeVisible();
  });

  it("shows bounded quota and fixed connection errors without reflecting native details", async () => {
    const modelGateway = gateway();
    vi.mocked(modelGateway.getSettings).mockResolvedValue({
      ...emptySnapshot,
      script: { ...emptySnapshot.script, configured: true },
    });
    const user = userEvent.setup();
    render(<ModelServiceSettings gateway={modelGateway} />);
    await screen.findByText("文案模型服务");
    const section = purposeSection("文案模型服务");
    await user.click(within(section).getByRole("button", { name: /测试连接/u }));
    expect(await within(section).findByText("连接成功；剩余请求 42 次，剩余 Token 1,234。"))
      .toBeVisible();

    vi.mocked(modelGateway.testConnection).mockRejectedValueOnce(
      new ModelServiceGatewayError("authentication_rejected", false),
    );
    await user.click(within(section).getByRole("button", { name: /测试连接/u }));
    expect(await within(section).findByText("密钥未通过阿里百炼验证，请检查后重新保存。"))
      .toBeVisible();
    expect(document.body).not.toHaveTextContent("private-native-secret");
  });
});
