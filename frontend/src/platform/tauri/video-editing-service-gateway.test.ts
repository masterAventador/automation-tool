import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import { VideoEditingServiceGatewayError } from "../../features/settings/video-editing-service-gateway";
import { TauriVideoEditingServiceGateway } from "./video-editing-service-gateway";

const emptySnapshot = {
  provider: "aliyun_ims",
  providerLabel: "阿里云视频剪辑服务",
  catalogVerifiedAt: "2026-07-23",
  configured: false,
  region: null,
} as const;

describe("Tauri video editing service gateway", () => {
  beforeEach(() => invoke.mockReset());

  it("uses only fixed native commands and returns public configuration state", async () => {
    invoke
      .mockResolvedValueOnce(emptySnapshot)
      .mockResolvedValueOnce({ ...emptySnapshot, configured: true, region: "cn-shanghai" })
      .mockResolvedValueOnce(emptySnapshot)
      .mockResolvedValueOnce({ region: "cn-shanghai", status: "connected" });
    const gateway = new TauriVideoEditingServiceGateway();

    await gateway.getSettings();
    await gateway.configure({
      region: "cn-shanghai",
      accessKeyId: "LTAI5tVe04TestAccessKey",
      accessKeySecret: "ve04PrivateSecret1234567890",
      ossBucket: "automation-tool-video-staging",
    });
    await gateway.clear();
    await expect(gateway.testConnection()).resolves.toEqual({
      region: "cn-shanghai",
      status: "connected",
    });

    expect(invoke.mock.calls).toEqual([
      ["get_video_editing_service_settings", undefined],
      [
        "configure_video_editing_service",
        {
          request: {
            region: "cn-shanghai",
            accessKeyId: "LTAI5tVe04TestAccessKey",
            accessKeySecret: "ve04PrivateSecret1234567890",
            ossBucket: "automation-tool-video-staging",
          },
        },
      ],
      ["clear_video_editing_service", undefined],
      ["test_video_editing_service_connection"],
    ]);
  });

  it("rejects snapshots that leak extra fields or unknown regions", async () => {
    for (const payload of [
      { ...emptySnapshot, accessKeyId: "leak" },
      { ...emptySnapshot, region: "cn-qingdao" },
      { ...emptySnapshot, provider: "other" },
      { ...emptySnapshot, configured: true, region: null },
      null,
      [],
    ]) {
      invoke.mockResolvedValueOnce(payload);
      const gateway = new TauriVideoEditingServiceGateway();
      await expect(gateway.getSettings()).rejects.toMatchObject({
        code: "protocol_mismatch",
      });
    }
  });

  it("maps native error codes and hides unknown failures", async () => {
    const gateway = new TauriVideoEditingServiceGateway();

    invoke.mockRejectedValueOnce({ code: "permission_denied", retryable: false });
    await expect(gateway.getSettings()).rejects.toMatchObject({
      code: "permission_denied",
    });

    invoke.mockRejectedValueOnce(new Error("raw native panic with secret paths"));
    const failure = await gateway.getSettings().catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(VideoEditingServiceGatewayError);
    expect((failure as VideoEditingServiceGatewayError).code).toBe("operation_unavailable");
    expect(String(failure)).not.toContain("secret paths");
  });

  it("keeps the native code when the error carries its readable message", async () => {
    const gateway = new TauriVideoEditingServiceGateway();

    invoke.mockRejectedValueOnce({
      code: "permission_denied",
      message: "native command error: permission_denied",
      retryable: false,
    });
    await expect(gateway.getSettings()).rejects.toMatchObject({
      code: "permission_denied",
      retryable: false,
    });

    invoke.mockRejectedValueOnce({
      code: "permission_denied",
      message: "accessKeyId=private-native-secret",
      retryable: false,
    });
    const reworded = await gateway.getSettings().catch((error: unknown) => error);
    expect(reworded).toMatchObject({ code: "permission_denied" });
    expect(JSON.stringify(reworded)).not.toContain("private-native-secret");
  });

  it("rejects malformed connection snapshots", async () => {
    invoke.mockResolvedValueOnce({ region: "cn-shanghai", status: "connected", extra: 1 });
    const gateway = new TauriVideoEditingServiceGateway();
    await expect(gateway.testConnection()).rejects.toMatchObject({
      code: "protocol_mismatch",
    });
  });
});
