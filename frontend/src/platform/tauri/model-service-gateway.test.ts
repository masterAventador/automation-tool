import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import { ModelServiceGatewayError } from "../../features/settings/model-service-gateway";
import { TauriModelServiceGateway } from "./model-service-gateway";

const emptySnapshot = {
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
} as const;

describe("Tauri model service gateway", () => {
  beforeEach(() => invoke.mockReset());

  it("uses only fixed native commands and returns public configuration state", async () => {
    invoke
      .mockResolvedValueOnce(emptySnapshot)
      .mockResolvedValueOnce({
        ...emptySnapshot,
        script: { ...emptySnapshot.script, configured: true, modelId: "glm-5.2" },
      })
      .mockResolvedValueOnce({
        ...emptySnapshot,
        script: { ...emptySnapshot.script, configured: true },
        videoCreative: { ...emptySnapshot.videoCreative, configured: true },
        sameCredential: true,
      })
      .mockResolvedValueOnce(emptySnapshot)
      .mockResolvedValueOnce({
        purpose: "script",
        modelId: "qwen3.7-max-2026-06-08",
        status: "connected",
        quota: { remainingRequests: 42, remainingTokens: null },
      });
    const gateway = new TauriModelServiceGateway();

    await gateway.getSettings();
    await gateway.configure({
      purpose: "script",
      modelId: "glm-5.2",
      apiKey: "sk-private-value-never-returned",
    });
    await gateway.reuseScriptForVideo();
    await gateway.clear("script");
    await expect(gateway.testConnection("script")).resolves.toMatchObject({
      status: "connected",
      quota: { remainingRequests: 42 },
    });

    expect(invoke.mock.calls).toEqual([
      ["get_model_service_settings", undefined],
      [
        "configure_model_service",
        {
          request: {
            purpose: "script",
            modelId: "glm-5.2",
            apiKey: "sk-private-value-never-returned",
          },
        },
      ],
      ["reuse_script_model_service_for_video", undefined],
      ["clear_model_service", { purpose: "script" }],
      ["test_model_service_connection", { purpose: "script" }],
    ]);
  });

  it("rejects private native fields, invalid purpose models and unbounded quotas", async () => {
    const gateway = new TauriModelServiceGateway();
    invoke.mockResolvedValueOnce({ ...emptySnapshot, apiKey: "sk-private" });
    await expect(gateway.getSettings()).rejects.toMatchObject({ code: "protocol_mismatch" });

    invoke.mockResolvedValueOnce({
      ...emptySnapshot,
      videoCreative: { ...emptySnapshot.videoCreative, modelId: "glm-5.2" },
    });
    await expect(gateway.getSettings()).rejects.toMatchObject({ code: "protocol_mismatch" });

    invoke.mockResolvedValueOnce({ ...emptySnapshot, sameCredential: true });
    await expect(gateway.getSettings()).rejects.toMatchObject({ code: "protocol_mismatch" });

    invoke.mockResolvedValueOnce({
      purpose: "script",
      modelId: "glm-5.2",
      status: "connected",
      quota: { remainingRequests: Number.MAX_SAFE_INTEGER, remainingTokens: null },
    });
    await expect(gateway.testConnection("script")).rejects.toMatchObject({
      code: "protocol_mismatch",
    });
  });

  it("maps only fixed native error fields and never reflects secrets", async () => {
    const gateway = new TauriModelServiceGateway();
    invoke.mockRejectedValueOnce({
      code: "authentication_rejected",
      retryable: false,
      apiKey: "sk-private-native-secret",
    });
    const error = await gateway.getSettings().catch((value: unknown) => value);
    expect(error).toBeInstanceOf(ModelServiceGatewayError);
    expect(error).toMatchObject({ code: "operation_unavailable" });
    expect(String(error)).not.toContain("private-native-secret");

    invoke.mockRejectedValueOnce({ code: "timed_out", retryable: true });
    await expect(gateway.testConnection("video_creative")).rejects.toMatchObject({
      code: "timed_out",
      retryable: true,
    });
  });
});
