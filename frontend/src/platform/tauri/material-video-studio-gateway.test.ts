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

  it("strictly reconciles jobs and sends only opaque identifiers for mutations", async () => {
    const gateway = new TauriMaterialVideoStudioGateway();
    const renderJobId = "3d594650-b5f4-4498-8e38-0cf85d6dfa72";
    const artifactId = "0f48954d-2df1-4168-8f33-b62c5772845a";
    invoke.mockResolvedValueOnce([{
      renderJobId,
      revision: 2,
      status: "succeeded",
      progressPercent: 100,
      subject: "新品介绍",
      artifactId,
      artifactSizeBytes: 2048,
      failureCode: null,
    }]);
    await expect(gateway.jobs()).resolves.toHaveLength(1);
    expect(invoke).toHaveBeenCalledWith("get_material_render_jobs");

    invoke.mockResolvedValueOnce(undefined);
    await gateway.cancel(renderJobId);
    expect(invoke).toHaveBeenCalledWith("cancel_material_render_job", { renderJobId });

    invoke.mockResolvedValueOnce(undefined);
    await gateway.deleteArtifact(artifactId);
    expect(invoke).toHaveBeenCalledWith("delete_material_video_artifact", { artifactId });
  });

  it("rejects unbounded or path-bearing job projections", async () => {
    invoke.mockResolvedValueOnce([{
      renderJobId: "3d594650-b5f4-4498-8e38-0cf85d6dfa72",
      revision: 1,
      status: "running",
      progressPercent: 10,
      subject: "任务",
      artifactId: null,
      artifactSizeBytes: null,
      failureCode: null,
      outputPath: "/private/video.mp4",
    }]);
    await expect(new TauriMaterialVideoStudioGateway().jobs()).rejects.toMatchObject({
      code: "protocol_mismatch",
    });
  });
});
