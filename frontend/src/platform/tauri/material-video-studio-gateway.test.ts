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

  it("keeps the native code when the error carries its readable message", async () => {
    const gateway = new TauriMaterialVideoStudioGateway();

    invoke.mockRejectedValueOnce({
      code: "configuration_required",
      message: "native command error: configuration_required",
      retryable: false,
    });
    await expect(gateway.open()).rejects.toMatchObject({
      code: "configuration_required",
      retryable: false,
    });

    invoke.mockRejectedValueOnce({
      code: "storage_unavailable",
      message: "apiKey=sk-never-reflect",
      retryable: false,
    });
    const reworded = await gateway.open().catch((value: unknown) => value);
    expect(reworded).toMatchObject({ code: "storage_unavailable" });
    expect(JSON.stringify(reworded)).not.toContain("never-reflect");
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

  it("uses narrow native commands for a manual motion RenderJob and its imported artifact", async () => {
    const gateway = new TauriMaterialVideoStudioGateway();
    const request = {
      creationMode: "manual_template_v1" as const,
      subject: "新品发布",
      stylePresetId: "blue-professional",
      primaryColor: "#1234ab",
      secondaryColor: "#f2eadb",
      secondsPerBeat: 4,
      beats: [
        { title: "增长看得见", caption: "字幕：本周销售增长 38%" },
        { title: "来自续费", caption: "字幕：客户持续选择我们" },
        { title: "下一步行动", caption: "字幕：立即查看新版能力" },
      ],
      logo: null,
    };
    invoke.mockResolvedValueOnce({
      renderJobId: "f89d8f18-6b4e-4f5a-8325-8da45f71d7e2",
      revision: 1,
      status: "queued",
      progressPercent: 5,
      subject: "新品发布",
      styleDisplayName: "商务蓝",
      artifactId: null,
      artifactSizeBytes: null,
      failureCode: null,
    });
    await expect(gateway.submitMotionDraft(request)).resolves.toMatchObject({
      status: "queued",
      subject: "新品发布",
    });
    expect(invoke).toHaveBeenCalledWith("submit_motion_video_draft", { request });

    invoke.mockResolvedValueOnce({
      artifactId: "2c29395b-1015-43ae-84a7-6f1901caac09",
      mediaType: "video/mp4",
      base64: "AAAA",
    });
    await expect(
      gateway.readMotionArtifact("2c29395b-1015-43ae-84a7-6f1901caac09"),
    ).resolves.toMatchObject({ mediaType: "video/mp4" });
    expect(invoke).toHaveBeenCalledWith("read_motion_video_artifact", {
      artifactId: "2c29395b-1015-43ae-84a7-6f1901caac09",
    });
  });

  it("accepts a still-image render reported with its own failure code", async () => {
    // The gate that catches a film whose frames never change reports
    // `static_render`. A gateway allowlist that does not know the code turns a
    // precise, actionable failure into a rejected snapshot, which reads to the
    // user as the job vanishing.
    const gateway = new TauriMaterialVideoStudioGateway();
    invoke.mockResolvedValueOnce([
      {
        renderJobId: "f89d8f18-6b4e-4f5a-8325-8da45f71d7e2",
        revision: 3,
        status: "failed",
        progressPercent: 55,
        subject: "新品发布",
        styleDisplayName: "商务蓝",
        artifactId: null,
        artifactSizeBytes: null,
        failureCode: "static_render",
      },
    ]);
    await expect(gateway.motionJobs()).resolves.toMatchObject([
      { status: "failed", failureCode: "static_render" },
    ]);
  });

  it("refuses a storyboard outside the declared duration budget before touching the native command", async () => {
    const gateway = new TauriMaterialVideoStudioGateway();
    const beat = (index: number) => ({
      title: `第 ${index} 段`,
      caption: `字幕：第 ${index} 段说明`,
    });
    const base = {
      creationMode: "manual_template_v1" as const,
      subject: "新品发布",
      stylePresetId: "blue-professional",
      primaryColor: "#1234ab",
      secondaryColor: "#f2eadb",
      secondsPerBeat: 4,
      beats: [beat(1), beat(2), beat(3)],
      logo: null,
    };

    for (const request of [
      { ...base, beats: [] },
      { ...base, beats: Array.from({ length: 11 }, (_, index) => beat(index + 1)) },
      { ...base, secondsPerBeat: 0 },
      { ...base, secondsPerBeat: 11 },
      { ...base, secondsPerBeat: 2.5 },
      // Both factors are legal on their own; only their product is not.
      { ...base, secondsPerBeat: 6, beats: Array.from({ length: 6 }, (_, i) => beat(i + 1)) },
    ]) {
      await expect(gateway.submitMotionDraft(request)).rejects.toMatchObject({
        code: "protocol_mismatch",
      });
    }
    expect(invoke).not.toHaveBeenCalled();
  });
});
