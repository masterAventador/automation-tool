import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import { MaterialVideoStudioGatewayError } from "../../features/video-studio/material-video-studio-gateway";
import { TauriMaterialVideoStudioGateway } from "./material-video-studio-gateway";

describe("Tauri material video studio gateway", () => {
  beforeEach(() => invoke.mockReset());

  it("opens the fixed native command and returns only public state", async () => {
    invoke.mockResolvedValueOnce({ state: "opened", modelId: "qwen3.7-max-2026-06-08" });
    const gateway = new TauriMaterialVideoStudioGateway();

    await expect(gateway.open()).resolves.toEqual({
      state: "opened",
      modelId: "qwen3.7-max-2026-06-08",
    });
    expect(invoke).toHaveBeenCalledWith("open_material_video_studio");
  });

  it("rejects private endpoint fields instead of exposing localhost", async () => {
    invoke.mockResolvedValueOnce({
      state: "opened",
      modelId: "qwen3.7-max-2026-06-08",
      endpoint: "http://127.0.0.1:49152/private",
    });
    const error = await new TauriMaterialVideoStudioGateway().open().catch((value: unknown) => value);

    expect(error).toBeInstanceOf(MaterialVideoStudioGatewayError);
    expect(error).toMatchObject({ code: "protocol_mismatch", retryable: false });
    expect(String(error)).not.toContain("127.0.0.1");
  });

  it("maps only fixed native errors without reflecting unknown details", async () => {
    const gateway = new TauriMaterialVideoStudioGateway();
    invoke.mockRejectedValueOnce({ code: "configuration_required", retryable: false });
    await expect(gateway.open()).rejects.toMatchObject({
      code: "configuration_required",
      retryable: false,
    });

    invoke.mockRejectedValueOnce({
      code: "process_unavailable",
      retryable: true,
      apiKey: "sk-never-reflect",
    });
    const error = await gateway.open().catch((value: unknown) => value);
    expect(error).toMatchObject({ code: "operation_unavailable", retryable: false });
    expect(String(error)).not.toContain("never-reflect");
  });
});
